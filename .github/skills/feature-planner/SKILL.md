---
name: feature-planner
description: Decompose high-level feature requests into detailed, repo-targeted PBIs for the Android Auth multi-repo project. Use this skill when a developer describes a feature at a high level and wants it broken down into actionable work items. Triggers include "plan this feature", "break this down into PBIs", "decompose this into tasks", "plan implementation for", or any request to turn a feature idea into structured development work items. This skill produces a plan for developer review — actual ADO work item creation is handled by the `pbi-creator` skill.
---

# Feature Planner

Decompose high-level feature descriptions into detailed, repo-targeted PBIs for the Android Auth
multi-repo codebase. Produce a structured plan for developer review.

**This skill does NOT create ADO work items.** It produces a structured PBI plan that the
developer reviews and approves. Once approved, the `pbi-creator` skill handles ADO work item
creation, iteration/area path discovery, and dependency linking.

## Prerequisites

- Developer provides: feature description and priority
- No ADO MCP tools required — this skill is pure planning

## Repository Routing

Determine which repo(s) each PBI targets based on the architectural layer:

| Module | GitHub Repo | When to Target |
|--------|-------------|----------------|
| common | `AzureAD/microsoft-authentication-library-common-for-android` | Shared utilities, IPC logic, data models, base classes, command architecture, token cache, crypto, telemetry primitives, AIDL contracts |
| common4j | (same repo as common) | Pure Java/Kotlin shared logic with no Android dependency |
| msal | `AzureAD/microsoft-authentication-library-for-android` | Client-facing API, AcquireToken/AcquireTokenSilent flows, MSAL-specific controllers, public configuration |
| broker | `identity-authnz-teams/ad-accounts-for-android` | Broker-side auth processing, PRT acquisition/rotation, device registration, eSTS communication, IPC entry points |
| broker4j | (same repo as broker) | Pure Java/Kotlin broker business logic, Protobuf schemas |
| adal | `AzureAD/azure-activedirectory-library-for-android` | Legacy ADAL changes only (rare — maintenance mode, bug fixes only) |
| 1ES-Pipelines | `IdentityDivision/Engineering/_git/AuthClientAndroidPipelines` (ADO) | Pipeline YAML changes: release orchestration, hotfix pipelines, templates, validation, publishing, scripts |

**Routing heuristic:**
1. If it touches IPC contracts, shared data models, or command architecture → `common`
2. If it's a client-facing API change or MSAL configuration → `msal`
3. If it handles token processing on the broker side, PRT, device registration → `broker`
4. If it's a pure Java utility with no Android dependency → `common4j` or `broker4j`
5. Most features span `common` + one consumer (`msal` or `broker`) — create separate PBIs for each

## Workflow

### Step 1: Check for Approved Design

Before decomposing into PBIs, check if an approved design spec exists:

1. Check `design-docs/` for a matching design: `ls design-docs/ | grep -i "<feature keyword>"`
2. If a design exists and is approved (merged PR), use it as the primary source for decomposition.
3. If no design exists, ask the developer:
   > "No design spec found for this feature. Would you like me to create one first using the
   > `design-author` skill? This is recommended for features that span multiple repos or
   > introduce new APIs."
4. If the developer wants a design first, hand off to the `design-author` skill and stop.
5. For small, single-repo changes (bug fixes, minor enhancements), skip design and proceed directly.

### Step 2: Understand the Feature

Gather from the developer:
1. **What** the feature does (functional description)
2. **Why** it's needed (user problem, business reason)
3. **Which flows** it affects (AcquireToken, AcquireTokenSilent, PRT, device registration, etc.)
4. **Scope boundaries** (what's in/out)

If the feature description is vague, use the `prompt-refiner` skill to structure it first.

### Step 3: Research Current Implementation

Use the `codebase-researcher` skill to understand:
- How related functionality currently works
- Which repos/files would need changes
- Existing patterns to follow (feature flags, error handling, telemetry)
- Test patterns in the affected areas

### Step 4: Decompose into PBIs

Break the feature into PBIs following these rules:

1. **One PBI per repo** — never create a PBI that spans multiple repos
2. **Dependency ordering** — if PBI-B depends on PBI-A, document the dependency explicitly
3. **Right-sized** — each PBI should be implementable by a single Copilot coding agent session (roughly 1-3 files changed, <500 lines)
4. **Self-contained description** — the PBI description must contain everything the coding agent needs to implement it without additional context
5. **No local file paths** — never reference `design-docs/` or other local workspace paths in PBI descriptions. The Copilot coding agent runs in a cloud environment with only the target repo cloned — it cannot access the super-repo, design docs, or other sub-repos. Inline all necessary context directly into the PBI description.

### Step 5: Write PBI Descriptions

Each PBI description MUST follow the template in `references/pbi-template.md`.

Key sections:
- **Objective**: What to implement and where
- **Target Repository**: Explicit repo URL + base branch
- **Context**: Why this change is needed, how it fits in the feature
- **Technical Requirements**: Specific implementation guidance
- **Acceptance Criteria**: Concrete, verifiable checklist
- **Dependencies**: Use PBI numbers like "PBI-1", not AB# IDs (those don't exist yet).
  The `pbi-creator` skill resolves these to AB# IDs after creation.
- **Files to Modify/Create**: Specific file paths when known

### Step 6: Present Plan for Review

Present the full plan using the **exact output format below**. This format is designed to be:
- **Human-readable**: developers can scan the summary table and expand individual PBIs for detail
- **Handoff-ready**: the `pbi-creator` skill can extract all fields needed to create ADO work items

**IMPORTANT**: Use this exact format — the `pbi-creator` skill depends on it.

#### Output Format

The output MUST contain these sections in this order:

**1. Header with metadata:**

```markdown
## Feature Plan: [Feature Name]

**Feature flag**: `[flag_name]` (or "N/A")
**Design PR**: [link] (or "N/A")
**Total PBIs**: [N]
```

**2. Dependency graph** (ASCII art showing parallel/sequential relationships):

```markdown
### Dependency Graph

PBI-1 (common) → PBI-2 (broker) + PBI-3 (msal) [parallel after PBI-1]
                  PBI-2 → PBI-4 (broker)
```

**3. Summary table** (one row per PBI, for quick scanning):

```markdown
### Summary Table

| PBI | Title | Repo | Module | Priority | Depends On |
|-----|-------|------|--------|----------|------------|
| PBI-1 | [title] | common | common4j + common | P1 | None |
| PBI-2 | [title] | broker | broker4j | P1 | PBI-1 |
| PBI-3 | [title] | msal | msal | P2 | PBI-1 |
```

**4. Dispatch order** (sequenced for the `pbi-dispatcher` skill):

```markdown
### Dispatch Order

1. Dispatch **PBI-1** first (no blockers)
2. After PBI-1 merges → dispatch **PBI-2** and **PBI-3** in parallel
3. After PBI-2 merges → dispatch **PBI-4**
```

**5. PBI details** — one block per PBI with metadata header + full description:

Each PBI detail block MUST have this structure:

```markdown
---

#### PBI-1: [Title]

| Field | Value |
|-------|-------|
| **Repo** | `[org/repo-name]` |
| **Module** | `[module]` |
| **Priority** | P[1-3] |
| **Depends on** | None / PBI-X |
| **Tags** | `ai-generated; copilot-agent-ready; [feature-tag]` |

##### Description

[Write the full PBI description here in PLAIN MARKDOWN — NOT HTML.
This section should contain: Objective, Context, Technical Requirements,
Acceptance Criteria, Files to Modify, Dependencies — per pbi-template.md.
The pbi-creator skill will convert this to HTML when creating ADO work items.]
```

**Why this structure?**
- The **metadata table** above the description lets the developer quickly scan each PBI.
- The **description in plain markdown** renders cleanly in VS Code chat (unlike HTML tags
  like `<details>` or `<h2>` which show as raw text in chat).
- The `pbi-creator` skill converts the markdown description to HTML when creating ADO work items.
- The **Summary Table** gives the developer a bird's eye view to approve the breakdown before
  seeing any details.

**IMPORTANT**: Do NOT use HTML tags (`<details>`, `<summary>`, `<h2>`, `<p>`, `<ul>`, etc.)
in the plan output. VS Code chat renders markdown only — HTML tags appear as raw text and
make the output unreadable. Use standard markdown formatting instead.

**6. Notes** (cross-repo coordination, external team notifications, etc.):

```markdown
### Notes

- [Cross-repo coordination needed]
- [OneAuth team notification if applicable]
```

**7. Handoff prompt** — always end with:

```markdown
### Next Step

> Plan approved? Say **"create the PBIs"** to trigger the `pbi-creator` skill,
> which will discover your ADO area path and iteration, then create all work items.
```

## Common Patterns

### Single-Repo Feature
Most bug fixes and small features only touch one repo. Create a single PBI.

### Two-Repo Feature (Common + Consumer)
The most common multi-repo pattern:
1. PBI-1: Add shared logic/contract in `common`
2. PBI-2: Consume from `msal` or `broker`

### Three-Repo Feature (Common + Broker + MSAL)
For end-to-end features affecting the full auth flow:
1. PBI-1: Add shared contract/data model in `common`
2. PBI-2: Implement broker-side processing (depends on PBI-1)
3. PBI-3: Implement MSAL client-side API (depends on PBI-1)
4. PBI-4: (optional) Integration test PBI

### Feature Flag Convention
All PBIs for a feature should use the **same feature flag name** across repos:
- Flag name format: `ExperimentationFeatureFlag.<FEATURE_NAME>`
- Include the flag name in each PBI description
