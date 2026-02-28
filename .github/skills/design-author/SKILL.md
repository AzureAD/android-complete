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
- **Feature flag** — Flag name and gating strategy
- **Telemetry** — Span names, attributes, success/failure signals
- **Testing strategy** — Unit test approach, instrumented test needs, E2E coverage
- **Rollout plan** — Feature flag stages, ECS configuration
- **Cross-repo impact** — Which repos need changes and in what order

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

### Step 5: Push as Draft PR for Review

Immediately after writing the spec, create a branch, push, and open a **draft PR** so the
developer can use ADO's inline commenting UI for real review feedback.

```bash
cd design-docs/
git checkout -b design/<feature-name-kebab-case>
git add "[Android] <Feature Name>"
git commit -m "Add design spec: <Feature Name>"
git push origin design/<feature-name-kebab-case>
```

Open a draft PR:

**Option A — Via ADO MCP Server** (if `repositories` tools are available):
Use the ADO MCP repository tools to create a pull request in the `DevEx` project,
`AuthLibrariesApiReview` repo, targeting the `main` branch. Set it as draft if the API supports it.

**Option B — Via Azure DevOps web UI**:
Provide the developer with a direct link:
```
https://dev.azure.com/IdentityDivision/DevEx/_git/AuthLibrariesApiReview/pullrequestcreate?sourceRef=design/<feature-name>&targetRef=main
```
Remind the developer to mark it as **Draft** when creating.

### Step 6: Present Summary to Developer

```markdown
## Design Spec Draft PR Opened: [Feature Name]

**Local file**: `design-docs/[Android] <Feature Name>/<spec-name>.md`
**Branch**: `design/<feature-name>`
**Draft PR**: [link to PR]

### Summary
[2-3 sentence summary of the proposed design]

### Recommended Solution
[Brief description of the recommended option and why]

### How to Review
1. Open the draft PR link above
2. Use ADO's inline commenting to leave feedback on specific lines
3. When done, say: **"address my design review comments"**
4. I'll read the PR comments via the ADO MCP server and update the spec accordingly

When the team approves, say: **"Design is approved, proceed with implementation"**
```

### Step 7: Address PR Review Comments

When the developer asks to address review comments:

1. Use the ADO MCP Server repository tools to read PR thread comments
2. For each comment:
   - Understand the feedback
   - Edit the local design spec to address it
   - Reply to the PR thread confirming the resolution
3. Commit and push the updates to the same branch
4. Report a summary of changes made

### Step 8: Proceed to Implementation (on approval)

When the developer confirms the design is approved:
1. The PR can be completed/merged in ADO
2. Hand off to the `feature-planner` skill for PBI decomposition

## Integration with Feature Planner

When the developer confirms the design is approved, the `feature-planner` skill should:
1. Read the approved design spec from `design-docs/`
2. Use it as the primary source for PBI decomposition
3. Reference the design doc PR link in each PBI description
4. Ensure PBI acceptance criteria align with the design's requirements
