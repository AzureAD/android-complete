---
name: feature-planner
description: Decompose features into detailed, repo-targeted work items. Use when asked to "plan this feature", "break this down into PBIs", "decompose this into tasks". Produces a structured plan for developer review — actual work item creation is handled by pbi-creator.
---

# Feature Planner

Decompose features into detailed, right-sized work items for implementation.

**This skill does NOT create work items.** It produces a plan for developer review.
Once approved, the `pbi-creator` skill handles creation in your tracking system.

## Configuration

Read `.github/orchestrator-config.json` for:
- `repositories` — repo hosting details (slug, host, baseBranch, accountType)
- `modules` — module-to-repo mapping (each module has a `repo` key pointing to a repository)
- `design.docsPath` — where design specs are stored

## Repository Routing

Use the `modules` and `repositories` maps from config to route each work item:

```json
// Example from config:
"repositories": {
  "common-repo": { "slug": "org/common-repo", "baseBranch": "dev" },
  "service-repo": { "slug": "org/service-repo", "baseBranch": "main" }
},
"modules": {
  "core": { "repo": "common-repo", "path": "core/", "purpose": "Shared utilities" },
  "service": { "repo": "service-repo", "purpose": "Backend processing" }
}
```

Work items target a **module name**. To find the repo: `modules.<name>.repo` → `repositories.<repo>`.

**Routing heuristic:**
1. Shared contracts/data models/utilities → shared module
2. Client-facing API changes → client module
3. Server/service-side processing → service module
4. Most features span a shared module + one consumer — create separate work items for each

## Workflow

### Step 1: Check for Approved Design

1. Check configured `design.docsPath` for a matching design spec
2. If design exists and is approved, use it as the primary source
3. If no design exists, ask the developer whether to create one first
4. For small, single-repo changes, skip design and proceed directly

### Step 2: Understand the Feature

Gather:
1. **What** the feature does
2. **Why** it's needed
3. **Which flows** it affects
4. **Scope boundaries** (in/out)

### Step 3: Research Current Implementation

Use the `codebase-researcher` skill to understand:
- How related functionality currently works
- Which repos/files would need changes
- Existing patterns to follow
- Test patterns in affected areas

### Step 4: Decompose into Work Items

Rules:
1. **One work item per repo** — never span multiple repos
2. **Dependency ordering** — document dependencies explicitly
3. **Right-sized** — each should be implementable in one agent session (~1-3 files, <500 lines)
4. **Self-contained description** — everything the coding agent needs, inline
5. **No local file paths** — the coding agent runs in the cloud with only the target repo cloned

### Step 5: Write Descriptions

Each description MUST include:
- **Objective**: What to implement and where
- **Context**: Why this change is needed, how it fits the broader feature
- **Technical Requirements**: Specific implementation guidance — see mandatory rules below
- **Acceptance Criteria**: Concrete, verifiable checklist
- **Dependencies**: Use WI-N references (resolved to AB# later)
- **Files to Modify/Create**: Specific paths extracted from research (see rule below)
- **Testing**: What tests to write

#### ⚠️ MANDATORY: Preserve Technical Detail from Design Spec

The coding agent implements ONLY from the PBI description. It does NOT see the design spec,
codebase-context.md, or any other local file. Therefore:

**Every technical detail the agent needs to write correct code MUST be in the PBI.**

1. **API signatures**: If the design spec includes method signatures, class interfaces, enum values,
   or return types — copy them **verbatim** into the PBI. Do NOT summarize code into prose.

   **Bad** (prose summary — agent will guess the types wrong):
   > "Create AuthTabManager that wraps AuthTabIntent.registerActivityResultLauncher() and launch()"

   **Good** (exact signatures from design spec — agent uses correct types):
   > "Create `AuthTabManager` that wraps the AndroidX Browser 1.9.0 AuthTab API:
   > ```kotlin
   > // registerActivityResultLauncher returns ActivityResultLauncher<Intent>, NOT <Uri>
   > // callback receives AuthTabIntent.AuthResult, NOT Uri
   > fun registerLauncher(activity: ComponentActivity, callback: (AuthTabIntent.AuthResult) -> Unit): ActivityResultLauncher<Intent> {
   >     return AuthTabIntent.registerActivityResultLauncher(activity, callback)
   > }
   >
   > // launch() takes 3 params: launcher, uri, AND redirectScheme
   > fun launch(launcher: ActivityResultLauncher<Intent>, uri: Uri, redirectScheme: String) {
   >     AuthTabIntent.Builder().build().launch(launcher, uri, redirectScheme)
   > }
   > ```"

2. **Rationale for changes**: Explain WHY something needs to change, not just what. The agent
   makes better decisions when it understands the reason.

   **Bad**: "Change browserVersion from 1.7.0 to 1.9.0"

   **Good**: "Change `browserVersion` from `1.7.0` to `1.9.0` because AndroidX Browser 1.9.0
   introduces the `AuthTabIntent` API (Chrome 137+) which this feature depends on. Note: this
   version bump changes the `onNewIntent` signature in `ComponentActivity` from
   `onNewIntent(intent: Intent)` to `onNewIntent(intent: Intent?)` — any override in existing
   code (e.g., `SwitchBrowserActivity`) must be updated to match."

3. **Breaking side effects**: If a change in this PBI will break other code (even code not in
   scope for this PBI), document it explicitly so the agent can fix it or the planner can
   create a separate PBI.

   **Example**: "⚠️ Bumping browserVersion to 1.9.0 will break `SwitchBrowserActivity.onNewIntent()`
   because the signature changed. Fix the override signature in this same PBI."

4. **Third-party API details**: When wrapping a new library or API version, include:
   - The exact dependency coordinates and version
   - Key method signatures the agent needs to call (copied from docs or design spec)
   - Any gotchas or differences from the agent's likely assumptions
   - What the API returns and what types to expect

5. **Code snippets from design spec**: If the design spec contains pseudocode, class skeletons,
   or implementation patterns, include them in the PBI. The agent benefits enormously from
   seeing a code sketch — even if it's pseudocode.

#### ⚠️ MANDATORY: File Paths Rule

The **"Files to Modify/Create"** field MUST list specific file paths from the research findings.
This is the single most important factor in coding agent success — it tells the agent WHERE
to look instead of forcing it to search blindly.

**Good** (specific, extracted from research):
```
Files to Modify/Create:
- common/common/src/main/java/com/microsoft/identity/common/internal/net/HttpClient.java — add retry logic
- common/common/src/main/java/com/microsoft/identity/common/internal/flight/CommonFlight.java — add RETRY_ENABLED flag
- common/common/src/test/java/com/microsoft/identity/common/internal/net/HttpClientTest.java — new test class
```

**Bad** (vague, agent has to guess):
```
Files to Modify/Create:
- HTTP client module
- Flight definitions
- Tests
```

If the research didn't identify specific files for a task, state that explicitly:
```
Files to Modify/Create:
- Exact paths not identified during research — agent should search for [specific class/pattern]
  starting in [module/directory]
```

This gives the agent a starting point even when exact paths aren't known.

### Quality Checklist

Before finalizing each work item:
- [ ] Could someone unfamiliar implement it from the description alone?
- [ ] Does it explain WHY, not just WHAT? (rationale for every change)
- [ ] Is the scope clear with explicit exclusions?
- [ ] Are acceptance criteria concrete and testable?
- [ ] Is it right-sized? (1-3 files = ideal, >6 files = split it)
- [ ] Does "Files to Modify/Create" list specific paths from research?
- [ ] Are API signatures from the design spec included verbatim (not summarized to prose)?
- [ ] Are breaking side effects documented? (e.g., dependency bump breaks existing code)
- [ ] For third-party API wrapping: are exact method signatures and return types included?

### Step 6: Present Plan for Review

Use this **exact output format** — the `pbi-creator` skill depends on it.

**IMPORTANT**: Do NOT use HTML tags (`<details>`, `<summary>`, etc.) — VS Code chat
renders markdown only. HTML tags appear as raw text.

#### Output Format

**1. Header:**

```markdown
## Feature Plan: [Feature Name]

**Feature flag**: `[flag_name]` (or "N/A")
**Design spec**: [path] (or "N/A")
**Total work items**: [N]
```

**2. Dependency graph:**

```markdown
### Dependency Graph

WI-1 (common) → WI-2 (service) + WI-3 (client) [parallel after WI-1]
```

**3. Summary table:**

```markdown
### Summary Table

| # | Title | Repo | Module | Priority | Depends On |
|---|-------|------|--------|----------|------------|
| WI-1 | [title] | common | shared | P1 | None |
| WI-2 | [title] | service | backend | P1 | WI-1 |
```

**4. Dispatch order:**

```markdown
### Dispatch Order

1. Dispatch **WI-1** first (no blockers)
2. After WI-1 merges → dispatch **WI-2** and **WI-3** in parallel
```

**5. Work item details:**

```markdown
---

#### WI-1: [Title]

| Field | Value |
|-------|-------|
| **Repo** | `[org/repo-name]` |
| **Module** | `[module]` |
| **Priority** | P[1-3] |
| **Depends on** | None / WI-X |
| **Tags** | `ai-generated; copilot-agent-ready; [feature-tag]` |

##### Description

[Full description in PLAIN MARKDOWN with: Objective, Context, Technical Requirements,
Acceptance Criteria, Files to Modify, Testing]
```

**6. Next step:**

```markdown
### Next Step

> Plan approved? Say **"create the PBIs"** to create work items in your tracking system.
```

## Common Patterns

### Single-Repo Feature
One work item. Most bug fixes and small enhancements.

### Two-Repo Feature (Shared + Consumer)
1. WI-1: Add shared logic/contract
2. WI-2: Consume from client or service

### Multi-Repo Feature
1. WI-1: Shared contract/data model
2. WI-2: Service-side processing (depends on WI-1)
3. WI-3: Client-side API (depends on WI-1)
4. WI-4: (optional) Integration tests

### Feature Flag Convention
All work items for a feature should use the **same feature flag name** across repos.
Include the flag name in each description.
