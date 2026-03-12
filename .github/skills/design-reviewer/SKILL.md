---
name: design-reviewer
description: Address review comments on design spec markdown files. Use this skill when a developer has added review comments (via the VS Code Comment API or manually) and wants the AI to address them. Triggers include "address review comments", "handle my review", "review comments on", or any request to process inline review feedback on a design spec.
---

# Design Reviewer

Address review comments on design spec files.

## How Comments Are Stored

Comments are stored in a single well-known file:
```
.github/design-reviews/reviews.json
```

The file is a JSON dictionary keyed by relative spec path:
```json
{
  "reviews": {
    "design-docs/[Android] Feature Name/spec.md": [
      { "line": 30, "text": "Why is this needed?", "lineContent": "the line text" },
      { "line": 55, "text": "Is this backed by data?", "lineContent": "..." }
    ],
    "design-docs/[Android] Other Feature/spec.md": [
      { "line": 10, "text": "Clarify this", "lineContent": "..." }
    ]
  }
}
```

## Workflow

### Step 1: Read Review Comments

1. Read `.github/design-reviews/reviews.json`
2. If a specific spec path was mentioned in the prompt (e.g., "on `design-docs/.../spec.md`"),
   only process comments for that spec. Otherwise process all specs in the file.
3. If the reviews file doesn't exist or has no comments, tell the user:
   > "No review comments found. Add comments using the gutter icons in the editor."

### Step 2: Read Spec Context

For each comment, read ±5 lines around the comment's line number in the spec file.
This ensures you address the comment with full awareness of context.

### Step 3: Evaluate Each Comment

| Comment Type | How to Identify | Action |
|-------------|----------------|--------|
| **Genuine issue** | Points out a bug, inaccuracy, missing info | Update the spec |
| **Improvement** | Suggests better approach, more detail | Update if it improves clarity |
| **Question** | "why?", "what?", "how?" | Answer clearly. Update spec only if the answer should be documented |
| **Challenge** | "Are you sure?", "Is this correct?" | Verify against codebase. Update if wrong, explain if correct |
| **Acknowledgment** | "lol", "nice", "👍" | Acknowledge briefly, no change |

### Step 4: Apply Changes

For each comment requiring a spec update:
1. Read the current content around the target line
2. Make the edit using `replace_string_in_file`

### Step 5: Clean Up reviews.json

**IMPORTANT**: After addressing all comments for a spec, remove that spec's entry
from `reviews.json`. This prevents comments from being re-processed.

Read the current `reviews.json`, delete the key for the addressed spec(s), and write
the file back. If no reviews remain, delete the file entirely.

```python
# Pseudocode for cleanup:
reviews = read_json(".github/design-reviews/reviews.json")
del reviews["reviews"]["design-docs/.../spec.md"]  # remove addressed spec
if len(reviews["reviews"]) == 0:
    delete_file(".github/design-reviews/reviews.json")
else:
    write_json(".github/design-reviews/reviews.json", reviews)
```

### Step 6: Present Summary

Use this exact format:

```markdown
## Review Comments Addressed

---

### Comment 1: Line N — "[short quote]"

**Type**: Question / Issue / Improvement / Acknowledgment

**Action**: [What was done or why no change was needed]

---

### Comment 2: Line N — "..."

**Type**: ...

**Action**: ...
```

**Rules:**
- Use `###` heading for EVERY comment — never a table
- Use `---` separators between comments
- If a comment was addressed by editing the spec, mention what changed
- If no change needed, explain why
