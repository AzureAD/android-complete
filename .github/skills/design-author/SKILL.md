---
name: design-author
description: Create detailed design specs for Android Auth features and open them as PRs in the AuthLibrariesApiReview ADO repo. Use this skill when a developer describes a feature at a high level and wants a detailed design document / implementation spec created before coding begins. Triggers include "design this feature", "create a design spec", "write a design doc", "create an implementation plan", "I need a design review for", or any request to produce a formal design document for team review before implementation.
---

# Design Author

Create detailed design specs for Android Auth features, save them locally in `design-docs/`,
and open PRs in the `AuthLibrariesApiReview` ADO repo for team review.

## Prerequisites

- `design-docs/` repo cloned locally (run `git droidSetup` or clone manually from
  `https://dev.azure.com/IdentityDivision/DevEx/_git/AuthLibrariesApiReview`)
- ADO MCP Server running with `repositories` domain enabled (configured in `.vscode/mcp.json`)

## Design Docs Context

The `design-docs/` folder contains ~150+ design specs for the Android Auth platform.

**Important caveats about existing designs:**
- Designs on `main` may be outdated — last-minute PR discussions often cause code to deviate.
  Always verify proposed patterns against the **current codebase**, not just existing designs.
- Some designs exist only as unmerged PRs. Check open PRs in the repo for in-progress thinking.
- Use existing designs as **style reference and historical context**, not as ground truth for current behavior.

## Design Spec Template

Follow the repo's template at `design-docs/Template/template.md`. Key sections:

1. **Title** — Feature name
2. **Applicable to and priority** — Platform table (focus on Android column)
3. **Components** — Which repos/modules (MSAL, Common, Broker, etc.)
4. **Problem description** — User problem, business context, examples
5. **Requirements (Must)** — Key functional requirements
6. **System Qualities (Must)** — Performance, telemetry, security, supportability
7. **Goals & Principles (Desired)** — Aspirational design goals
8. **Solution options** — Multiple options with pseudo code, pros/cons
9. **Solution Decision** — Recommended option with reasoning

For Android-specific designs, also include:
- **API surface** — Public/internal classes, methods, parameters
- **Data flow** — Request/response flow across repos (MSAL → Common → Broker → eSTS)
- **IPC contract changes** — Any AIDL/Bundle schema changes
- **Feature flag** — Required default-off runtime flag contract; reasoned N/A only for non-runtime work
- **Telemetry** — Span names, attributes, success/failure signals
- **Testing strategy** — Unit test approach, instrumented test needs, E2E coverage
- **Rollout plan** — Feature flag stages, ECS configuration
- **Cross-repo impact** — Which repos need changes and in what order

### Human design approval surface

For the Android-complete Dev Design Dynamic Workflow, generate and publish a
separate **Human Design Review Brief** alongside the authoritative detailed design
for the same revision. Use the
[paired-document contract](../../extensions/DevDesignDoc/README.md#primary-human-design-review-brief-and-paired-publication):
decisions needed first, customer outcome/scope, plain-language architecture/flow,
recommendations/alternatives/tradeoffs, evidence versus assumptions, consequential
risks/safeguards and accountable owners/blockers. The brief is the primary human
approval surface; reviewers drill into linked detail rather than reading the entire
spec. Preserve the full specification and existing gates.

Independently check consistency/completeness; target ten minutes within the complete
rendered 1,800-word/16,000-character budget without silently omitting critical
information. Publish both artifacts together and bind human approval to their exact
paired revision. Regenerate/re-review both after substantive edits and highlight
changes since the verified prior human review; never reuse stale approval.
Design approval is not rollout permission. This human brief is distinct from the
design-time pre-mortem authoring brief and the later post-implementation pre-mortem.

### Required default-off feature flags

Every runtime feature, including small/single-repo features, needs the
[default-off contract](../feature-planner/references/pbi-template.md#required-default-off-feature-contract).
Retain a **Feature flags and isolation** section: source-verified owning framework,
flag key/owner, Boolean false code/configuration-failure/production defaults, all
guarded entry points, zero new off-path side effects, override/cache behavior,
approved enabled E2E cohort, rollback including active attempts and owned verification.
Map each runtime change to its flag(s); documentation/regression-only/deferred
work needs an explicit no-runtime-impact reason. Carry the contract into coding
PBI acceptance criteria. Do not invent APIs or inspect/change live production
configuration; design approval is not code verification or activation permission.

### Required pre-mortem and PBI linkage

For every Android-complete feature, including non-runtime features, add a
**Pre-mortem and operational readiness** section containing a design-time authoring
brief, not the pre-mortem draft. Capture feature-specific requirements/source
evidence, known risks, telemetry/rollback inputs, open gaps and proposed owners
for the later authoring PBI. Follow the content and acceptance contract
in [Required Feature Pre-mortem PBI](../feature-planner/references/pbi-template.md#required-feature-pre-mortem-pbi):
plan coverage of failure scenarios/customer impact; prevention/detection/response;
monitoring signals and later KQL drafts with contracts, cohorts, denominators and
coverage gaps; monitoring owner/cadence/stop criteria; rollback including active attempts; validation and
release prerequisites with owners.

Include a **Pre-mortem child PBI** field: use `Pending feature planning` before
backlog creation, or the existing AB# ID and durable URL when available. Feature
planning must include a dedicated child PBI to author/review the artifact, reusing
a suitable existing item, dependent on all feature implementation PBIs.
Record **Pre-mortem artifact: Pending implementation completion and PBI dispatch**.
Backlog creation links the PBI to the exact design/brief revision after approval.
Only after all feature implementation is complete may that PBI be dispatched.
The agent then writes the complete pre-mortem (including actual KQL drafts)
against implemented, pinned sources, links it back, and hands it to the named
human reviewer.
The human checks completeness/assumptions/owners/gaps and records approval;
agent completion or design approval is not pre-mortem authoring/review acceptance.
Do not draft it during design/implementation or require the future artifact to
approve the design, plan or implementation. Require human-reviewed pre-mortem
acceptance before release/production enablement, without authorizing either.

Separate document review from query validation and release approval. Explicitly
unexecuted pre-flight KQL and unresolved table/property mappings are acceptable
with named follow-up owners; disabled features may have no production data.
Require validation using an approved enabled E2E test cohort before monitoring
is relied on for rollout, not broad production enablement or activation merely
to finish the authoring PBI. Retain all existing design/plan approval gates.

### Accessibility design (required)

Retain an **Accessibility design** section in every spec, including when the
external template lacks it. For new or changed Authenticator UI, map the stable
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
2. Which auth flows it affects
3. Scope boundaries (in/out)
4. Any existing designs to reference (check `design-docs/` for related specs)

### Step 2: Research the Codebase

Use the `codebase-researcher` skill to:
- Understand how related functionality currently works
- Identify which repos/files would be affected
- Find existing patterns to follow (feature flags, error handling, telemetry, IPC contracts)
- Check for any existing design docs in `design-docs/` on the same topic

#### Baseline and necessity gates (including direct authoring)

Before drafting, pin relevant source revisions and record scenario traces from the
trigger and applicable flags/account types through callbacks, state/result changes,
observers, and completion/lifecycle handlers to the final observable outcome.
Include success, failure, cancellation, and retry branches relevant to the requirements.
For configuration/contract requirements, follow the configuration to its effect.
Do not splice revisions into a single execution path. Missing relevant source or an
unresolved terminal path is a blocking unknown, not evidence that behavior is absent.
Trace the current implementation, not an imaginary execution of future requirements:
an expected feature delta is not a source conflict. For externally owned UI, verify
the app handoff/return contract without claiming a particular server challenge order.
Keep release sign-off/operational gates explicit and separate from unresolved app
contracts. Final design approval is intentionally pending, not an input prerequisite.

For example, absence of an error-ending call in a telemetry callback does **not**
establish a defect if the caller subsequently posts failed results and a completion
observer ends the operation. Read that chain and preserve existing attribution and
ordering before proposing an extra callback.

### Step 3: Research Existing Designs

Search the `design-docs/` folder for related designs:
```
# Look for related designs
ls design-docs/ | grep -i "<keyword>"
# Read relevant designs for patterns and prior art
```

Android-specific designs are prefixed with `[Android]`. Pay attention to:
- Similar feature designs for structural patterns
- The level of detail expected
- How they handle cross-repo concerns

### Step 4: Write the Design Spec

Create the spec following the template. The file should be created at:
```
design-docs/[Android] <Feature Name>/<spec-name>.md
```

Use the standard template sections. For the **Solution options** section:
- Always provide at least 2 options
- Include pseudo code / API signatures for each
- List concrete pros/cons
- Make a clear recommendation in the Solution Decision section

### Step 5: Present Design for Review

Before marking a design review-ready, include a visible necessity register for every
implementation mandate (including incidental fixes): requirement, section, pinned
baseline trace, existing behavior, requested delta, necessity, inspected counterevidence,
and verification. Classify each entry as new feature work, verified defect, existing
behavior to preserve (regression-only), optional improvement (deferred), or unresolved.
Actively search downstream consumers, alternate paths, and tests for behavior that
disproves a "missing behavior" claim. Already-handled behavior must not become new
required work; unresolved necessity blocks readiness instead of being hidden as an assumption.

Use the `design-feature` workflow's independent source review before a validated
handoff. The reviewer must inspect the full paths and challenge each change, not just
verify that citations exist. Review new mandates after revisions too. Direct authoring
uses the same evidence/necessity checks; it is not a bypass for a failed or incomplete
workflow. Without independent review, label the artifact **unvalidated draft** and
record the missing review explicitly; do not claim readiness, approval, or a GO.
Human approval of the exact artifact remains separate from automated review.

Assess the Accessibility design section before handoff and after revisions.
Check every applicable requirement, screen/state and verification mapping, including
reused components and justified non-UI/N/A cases. Missing applicable design coverage
blocks readiness; planned runtime evidence must remain explicitly Unverified rather
than being treated as a pass.

After writing the spec, **STOP and present choices to the developer**. Do NOT auto-create
a PR or auto-proceed. Present the design summary and these explicit options:

```markdown
## Design Spec Written: [Feature Name]

**Local file**: `design-docs/[Android] <Feature Name>/<spec-name>.md`

### Summary
[2-3 sentence summary of the proposed design]

### Recommended Solution
[Brief description of the recommended option and why]

---

### What would you like to do?

1. **Review locally first** — I'll open the spec in the editor for you. Use the **+
   icons in the gutter** to add review comments on specific lines, then click the
   **status bar button** (bottom right) to submit them.

2. **Approve and skip PR** — Move directly to PBI planning without creating a design PR.
   Say: **"design approved, plan the PBIs"**

3. **Approve and open draft PR** — Push to AuthLibrariesApiReview repo as a **draft** PR
   for team review.
   Say: **"open a draft PR"**

4. **Approve and publish PR** — Push and open a **published** (non-draft) PR for team review.
   Say: **"open and publish the PR"**

5. **Request changes** — Tell me what to change and I'll update the spec.
```

**MANDATORY**: Wait for the developer to explicitly choose one of these options.
Do NOT auto-select any option.

### Step 5a: Local Review Workflow (if developer chooses option 1)

Open the spec file in the editor for the developer:
```powershell
code "design-docs/[Android] <Feature Name>/<spec-name>.md"
```

Then tell the developer:
> "The spec is open in the editor. Here's how to review:
> 1. Click the **+ icon** in the gutter next to any line to add a comment
> 2. Type your comment and click **Add Comment**
> 3. Comments auto-collapse — click the line indicator to expand
> 4. When done, click the **status bar button** at the bottom right
>    (it shows ‘💬 N Review Comments — Click to Submit’)
> 5. This sends your comments to chat and I'll address each one"

When the developer submits review comments (via the status bar), the design-reviewer
skill will be triggered automatically. After addressing, return to Step 5
(present choices again).

### Step 5b: Push and Create PR (if developer chooses option 3 or 4)

**Branch naming**: Use the developer's alias (discovered from `git config user.email` or
`.github/developer-local.json`) as the branch prefix:
```powershell
$alias = (git config user.email) -replace '@.*', ''
git checkout -b "$alias/design-<feature-name-kebab-case>"
```

```bash
cd design-docs/
git add "[Android] <Feature Name>"
git commit -m "Add design spec: <Feature Name>"
git push origin $BRANCH_NAME
```

**Create PR via ADO MCP Server** (if `repositories` tools are available):
- Set `isDraft: true` for option 3 (draft), `isDraft: false` for option 4 (published)
- **PR description**: Use actual line breaks or HTML formatting, NOT literal `\n` escape sequences
- Target branch: `main` (or `dev` depending on the repo's default)

Present the PR link and review instructions:
```markdown
### PR Created
**PR**: [link to PR]
**Status**: Draft / Published

### How to Review
1. Open the PR link above
2. Use ADO's inline commenting to leave feedback
3. When done, say: **"address my design review comments"**
4. I'll read the PR comments via the ADO MCP server and update the spec

When the team approves, say: **"design approved, plan the PBIs"**
```

### Step 6: Address PR Review Comments

When the developer asks to address review comments (from ADO PR):

1. Use the ADO MCP Server repository tools to read PR thread comments
2. For each comment:
   - Understand the feedback
   - Edit the local design spec to address it
   - Reply to the PR thread confirming the resolution
3. Commit and push the updates to the same branch
4. Report a summary of changes made
5. Return to Step 5 (present choices again)

### Step 7: Proceed to Implementation (on approval)

When the developer confirms the design is approved:
1. The PR can be completed/merged in ADO
2. Hand off to the `feature-planner` skill for PBI decomposition

## Integration with Feature Planner

When the developer confirms the design is approved, the `feature-planner` skill should:
1. Read the approved design spec from `design-docs/`
2. Use it as the primary source for PBI decomposition
3. Reference the design doc PR link in each PBI description
4. Ensure PBI acceptance criteria align with the design's requirements
