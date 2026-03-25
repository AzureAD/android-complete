---
name: pr-validator
description: Validate an agent-created PR against its PBI acceptance criteria. Use during the Monitor phase to check whether a PR satisfies what was requested before human review. Triggers include "validate PR", "check PR quality", "does this PR match the spec".
---

# PR Validator

Validate whether an agent-created PR satisfies its originating PBI's acceptance criteria
and follows project conventions. This runs during the Monitor phase — after the coding
agent creates a PR but before the human reviews it.

## Purpose

Save human review time by catching obvious gaps:
- Missing acceptance criteria
- Missing tests
- Convention violations that the agent should have followed
- Scope creep (changes beyond what was requested)

**This is NOT a full code review.** It's a structured checklist that flags what to look at.

## Inputs

- PR number and repo slug
- The PBI that originated the PR (AB# ID or from feature state)

## Process

### Step 1: Gather PR Data

```powershell
gh pr view <prNumber> --repo "<slug>" --json title,body,files,additions,deletions,commits,reviews,statusCheckRollup
```

Also get the diff stat:
```powershell
gh pr diff <prNumber> --repo "<slug>" --stat
```

### Step 2: Gather PBI Data

Read the originating PBI's description from feature state or ADO:

```powershell
$su = Join-Path $HOME ".feature-orchestrator" "state-utils.js"
node $su get-feature "<feature-name>"
```

Find the PBI that matches this PR (by repo + AB# reference in PR title/body).
Extract:
- **Acceptance Criteria** — the checklist from the PBI description
- **Files to Modify/Create** — expected file paths
- **Technical Requirements** — specific implementation guidance
- **Testing** — expected test coverage

### Step 3: Acceptance Criteria Check

For each acceptance criterion in the PBI:
1. Search the PR diff for evidence that it's addressed
2. Mark as: ✅ Addressed | ⚠️ Partially | ❌ Not found | ❓ Can't determine

**How to check:**
- If the criterion mentions a specific behavior → look for code implementing it
- If it mentions a specific file → check if that file is in the PR's changed files
- If it mentions tests → check if test files are included
- If it's too abstract to verify from diff alone → mark ❓

### Step 4: File Coverage Check

Compare the PBI's "Files to Modify/Create" against the PR's actual changed files:
- **Expected but not changed** → flag as potential gap
- **Changed but not expected** → flag as potential scope creep (may be fine — dependencies, imports)
- **New files created** → check naming conventions match the repo's patterns

### Step 5: Convention Check

Based on the repo's `.github/copilot-instructions.md` (which the agent should have followed),
spot-check:
- **Tests included?** If the PBI specified tests and no test files are in the diff → flag
- **Telemetry?** If the PBI mentioned telemetry/spans and no span-related code is visible → flag
- **Feature flag?** If the PBI mentioned a feature flag and none is visible → flag
- **License headers?** If new files were created, check for headers (don't read every file — just note if new files exist)

**Do NOT** do a full code review. Don't check variable naming, code style, or logic correctness.
The human reviewer does that. Focus only on structural completeness.

### Step 6: CI Status Check

```powershell
gh pr checks <prNumber> --repo "<slug>"
```

Report:
- All passing → ✅
- Some failing → list which checks failed
- Pending → note that CI is still running

### Step 7: Present Report

```markdown
## 🔍 PR Validation: #<prNumber> — <PR title>

**PBI**: AB#<id> — <title>
**Repo**: <slug>
**Changes**: +<additions> -<deletions> across <N> files

### Acceptance Criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | [criterion text] | ✅ Addressed | [file or code reference] |
| 2 | [criterion text] | ⚠️ Partial | [what's missing] |
| 3 | [criterion text] | ❌ Not found | — |

### File Coverage

| Expected (from PBI) | In PR? | Notes |
|---------------------|--------|-------|
| path/to/File.java | ✅ | Modified |
| path/to/Test.java | ❌ | Not in diff — tests may be missing |

**Unexpected changes**: [list files changed that weren't in the PBI, if any]

### Convention Checks

| Check | Status |
|-------|--------|
| Tests included | ✅ / ❌ |
| Telemetry spans | ✅ / ❌ / N/A |
| Feature flag gating | ✅ / ❌ / N/A |
| CI status | ✅ All passing / ❌ [failures] |

### Summary

**Overall**: 🟢 Looks good / 🟡 Review these gaps / 🔴 Significant gaps

[1-2 sentence summary: what the human reviewer should focus on]
```

## When to Run

- **Automatically**: When the Monitor phase detects a new PR from the coding agent
- **Manually**: When the user says "validate PR" or "check this PR"
- **On refresh**: When the dashboard refreshes PR status and a new open PR is found

## Important Guidelines

- **Speed over depth**: This should take <30 seconds. Don't read every line of code.
- **No false confidence**: If you can't verify a criterion from the diff, say ❓ not ✅
- **Actionable output**: Every ❌ or ⚠️ should tell the human what to look for
- **Don't block**: This is informational. Even if gaps exist, the human decides whether to approve
