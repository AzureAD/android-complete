---
name: feature-planner
description: Decompose features into detailed, repo-targeted work items.
---

# Feature Planner

Decompose features into right-sized, self-contained work items.

**This skill does NOT create work items.** It produces a plan for review.

## Rules

1. **One work item per repo** — never span multiple repos
2. **Self-contained descriptions** — the coding agent only has the description
3. **No local file paths** — agent runs in the cloud with only the target repo
4. **Right-sized** — 1-3 files per item, <500 lines

## Quality Checklist

- [ ] Could someone unfamiliar implement it from the description alone?
- [ ] Does it explain WHY, not just WHAT?
- [ ] Are acceptance criteria concrete and testable?
- [ ] Is it right-sized? (>6 files = split it)

## Output Format

**Do NOT use HTML tags** — VS Code chat renders markdown only.

```markdown
## Feature Plan: [Name]

**Feature flag**: `[flag]` (or "N/A")
**Total work items**: [N]

### Dependency Graph
WI-1 (shared) → WI-2 (service) + WI-3 (client)

### Summary Table
| # | Title | Repo | Priority | Depends On |
|---|-------|------|----------|------------|

### Dispatch Order
1. Dispatch **WI-1** first
2. After merge → **WI-2** and **WI-3** in parallel

---

#### WI-1: [Title]
| Field | Value |
|-------|-------|
| **Repo** | `org/repo` |
| **Priority** | P1 |
| **Depends on** | None |
| **Tags** | `ai-generated; copilot-agent-ready` |

##### Description
[Full description: Objective, Context, Technical Requirements,
Acceptance Criteria, Files to Modify, Testing]
```
