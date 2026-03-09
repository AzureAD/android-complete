---
name: design-author
description: Create detailed design specs for features.
---

# Design Author

Create detailed design specs, save locally, and optionally open PRs for review.

## Workflow

### Step 1: Understand the Feature
1. What the feature does and why
2. Which components/flows it affects
3. Scope boundaries (in/out)

### Step 2: Research the Codebase
Use the `codebase-researcher` skill to understand existing patterns.

### Step 3: Write the Design Spec

<!-- TODO: CUSTOMIZE — Update the save path for your project -->
Save to `docs/designs/<feature-name>/spec.md`.

Include:
1. Problem description
2. Requirements (functional + non-functional)
3. Solution options (at least 2) with pseudo code and pros/cons
4. Recommended solution with reasoning
5. API surface changes (if applicable)
6. Testing strategy
7. Cross-repo impact

### Step 4: Present for Review

Use `askQuestion` with 5 options:
1. Review locally (open in editor)
2. Approve & plan PBIs
3. Open draft PR
4. Open published PR
5. Request changes

**Wait for explicit choice. Do NOT auto-proceed.**
