# Work Item Template

Use this structure for every work item description. The description must be
**self-contained** — the coding agent only has this text plus the target repo.

## Objective

[1-2 sentences: What to implement and in which module/repo]

## Context

[Why this change is needed. How it fits into the larger feature.
Include enough background that someone unfamiliar could understand the motivation.]

## Technical Requirements

### What to Build

[Specific implementation details. Include:]

- Classes/functions to create or modify
- Method signatures and data structures
- Error handling approach
- Threading/concurrency model (if relevant)
- Integration points with other modules

### Code Patterns to Follow

[Reference existing patterns in the repo. Include actual code examples
or file paths within the TARGET REPO (not other repos).]

```
// Example of the pattern to follow:
class ExistingPattern {
    // show the convention
}
```

### What NOT to Do

[Explicit exclusions to prevent scope creep:]
- Do NOT modify [specific files/features outside scope]
- Do NOT add [unnecessary abstractions]
- This work item does NOT cover [related but separate concern]

## Acceptance Criteria

- [ ] [Concrete, testable criterion 1]
- [ ] [Concrete, testable criterion 2]
- [ ] [Concrete, testable criterion 3]
- [ ] All existing tests pass
- [ ] New unit tests cover the happy path and error cases
- [ ] Code follows project conventions (from copilot-instructions.md)

## Files to Modify

| File | Change |
|------|--------|
| `path/to/file.ext` | [What to change] |
| `path/to/new-file.ext` | [New file — what it contains] |

## Dependencies

- **Depends on**: [WI-N / AB#ID — what must be merged first and why]
- **Depended on by**: [WI-N — who is waiting for this]

## Testing

- Unit tests for [specific logic]
- Integration tests for [cross-component interaction] (if applicable)
- Test file location: `path/to/tests/`
