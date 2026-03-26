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
10. **Feature flag** — Flag name and gating strategy (if applicable)
11. **Telemetry** — Key metrics, span names, success/failure signals
12. **Testing strategy** — Unit tests, integration tests, E2E coverage
13. **Rollout plan** — Staged rollout, feature flag configuration
14. **Cross-repo impact** — Which repos need changes and in what order

If a template file exists at the configured `design.templatePath`, follow that instead.

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
