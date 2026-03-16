---
name: design-reviewer
description: Address review comments on design spec files. Use when a developer submits inline review comments and wants them addressed. Triggers include "address review comments", "handle my review", or review comment submission.
---

# Design Reviewer

Address review comments on design spec files.

## How Comments Are Stored

Comments are stored in:
```
.github/design-reviews/reviews.json
```

Format:
```json
{
  "reviews": {
    "path/to/spec.md": [
      { "line": 30, "text": "Why is this needed?", "lineContent": "the line text" }
    ]
  }
}
```

## Workflow

### Step 1: Read Review Comments

1. Read `.github/design-reviews/reviews.json`
2. If a specific spec was mentioned, only process that spec's comments
3. If no comments found:
   > "No review comments found. Add comments using the gutter icons in the editor."

### Step 2: Read Spec Context

For each comment, read ±5 lines around the comment's line number for full context.

### Step 3: Evaluate Each Comment

| Comment Type | How to Identify | Action |
|-------------|----------------|--------|
| **Genuine issue** | Points out bug, inaccuracy, missing info | Update the spec |
| **Improvement** | Suggests better approach | Update if it improves clarity |
| **Question** | "why?", "what?", "how?" | Answer clearly; update spec if answer should be documented |
| **Challenge** | "Are you sure?" | Verify against codebase; update if wrong, explain if correct |
| **Acknowledgment** | "nice", "👍" | Acknowledge briefly, no change |

### Step 4: Apply Changes

For each comment requiring a spec update:
1. Read the current content around the target line
2. Make the edit using `replace_string_in_file`

### Step 5: Clean Up reviews.json

After addressing all comments for a spec, remove that spec's entry from `reviews.json`.
If no reviews remain, delete the file.

### Step 6: Present Summary

```markdown
## Review Comments Addressed

---

### Comment 1: Line N — "[short quote]"

**Type**: Question / Issue / Improvement / Acknowledgment
**Action**: [What was done or why no change was needed]

---

### Comment 2: Line N — "..."
...
```

**Rules:**
- Use `###` heading for EVERY comment — never a table
- Use `---` separators between comments
- If spec was edited, mention what changed
- If no change needed, explain why
