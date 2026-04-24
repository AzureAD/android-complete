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
3. **Components** — Which repos/modules (MSAL, Common, Broker, Authenticator, etc.)
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
- **Feature flag** — Flag name and gating strategy
- **Telemetry** — Span names, attributes, success/failure signals
- **Testing strategy** — Unit test approach, instrumented test needs, E2E coverage
- **Rollout plan** — Feature flag stages, ECS configuration
- **Cross-repo impact** — Which repos need changes and in what order (include Authenticator if the feature affects the Authenticator app)

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
- Identify which repos/files would be affected (including Authenticator if the feature touches the app)
- Find existing patterns to follow (feature flags, error handling, telemetry, IPC contracts)
- Check for any existing design docs in `design-docs/` on the same topic

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
