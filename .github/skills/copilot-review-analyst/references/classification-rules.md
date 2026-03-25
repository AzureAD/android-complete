# Classification Rules

Complete classification hierarchy for Copilot review comment analysis.

## Classification Cascade (Priority Order)

Apply rules in this exact order. First match wins.

### Replied Comments (has human reply)

| Priority | Condition | Verdict |
|----------|-----------|---------|
| 1 | Reply matches positive keyword patterns | **Helpful** |
| 2 | Reply matches negative keyword patterns | **Not Helpful** |
| 3 | Both positive and negative matched (mixed) | Verdict from `mixedResponseVerdict` in manual audit file (default: **Not Helpful**) |
| 4 | Reply contains `@copilot` (delegated fix) | **Helpful** |
| 5 | Reply matches acknowledged-action patterns | **Helpful** |
| 6 | Reply matches explained-away patterns | **Not Helpful** |
| 7 | Reply matches outdated/dismissed patterns | **Not Helpful** |
| 8 | Genuinely unclear — apply AI judgment | See AI Classification below |

### No-Response Comments (no human reply)

| Priority | Condition | Verdict |
|----------|-----------|---------|
| 9 | `suggestion-applied` or `suggestion-likely-applied` (from diff verification) | **Helpful** |
| 10 | `exact-lines-modified` (from diff verification) | **Helpful** |
| 11 | `lines-modified-different-fix` | **Helpful** (nearby lines modified with a different approach — engineer addressed the concern) |
| 12 | `file-changed-elsewhere` or `file-changed-no-line-info` | **Check re-audit list** — helpful if evidence found, else **Unresolved** |
| 13 | `file-not-changed`, `no-subsequent-commits`, `not-applied` | **Unresolved** |
| 14 | Comment on stale/outdated code | **Not Helpful** |

## Keyword Patterns

### Positive Patterns (→ Helpful)

```
good catch, fixed, done, addressed, will fix, will address,
thanks, thank you, agreed, makes sense, updated, nice catch,
you're right, you are right, correct, valid point, great catch,
resolved, will do, good point, fair point, acknowledged,
applied, changed, modified, yep, absolutely,
i'll update, i will update, i'll fix, i will fix,
good suggestion, great suggestion, nice suggestion,
will change, will update, pushed a fix, committed,
good find, great find, indeed,
making the change, i've updated, i've fixed
```

### Negative Patterns (→ Not Helpful)

```
not applicable, n/a, won't fix, wontfix, by design,
intentional, false positive, not relevant, ignore,
doesn't apply, not needed, unnecessary, nah, no need,
disagree, incorrect, wrong, not accurate, hallucin,
not a real issue, not an issue, this is fine, it's fine,
already handled, already done, not applicable here,
copilot is wrong, bot is wrong, misunderstanding,
out of scope, does not apply, not a concern, not a problem,
doesn't matter, won't happen, can't happen, impossible
```

### Acknowledged-Action Patterns (→ Helpful, for unclear replies)

These indicate the engineer acted on the feedback even if they didn't use standard positive keywords:

```
added tests?, refactored, removed, reverted, renamed,
implemented, reworked, update signature, log warning,
move check, add test, add unit test, nice job, good bot
```

Use word-boundary matching (`\b`) for these.

### Explained-Away Patterns (→ Not Helpful, for unclear replies)

These indicate the engineer explained why the comment is not relevant:

```
this is, we don't, we do not, we aren't, nope, has been,
it's a, they're meant, this has, only used, never been,
was consciously, just telemetry, is just, original behavior,
overdo, legacy, can only, can never, doesn't need,
suffix was, timing is not, skip, most of the, empty is fine,
no longer, will stick, keep the current, consciously
```

### Outdated/Dismissed Patterns (→ Not Helpful)

```
outdated, dismissed
```

## AI Classification for Genuinely Unclear Replies

When no keyword pattern matches, read the reply text with domain context:

1. **Is the engineer confirming they'll act?** Even indirect signals like "addressing in a later commit", "implemented something similar", or linking a commit hash → **Helpful**
2. **Is the engineer explaining why the feedback doesn't apply?** Phrases like "this is just telemetry", "we consciously chose this", "legacy code" → **Not Helpful**
3. **Is the reply tangential or administrative?** E.g., "will consider in another PR" → **Helpful** if they acknowledge the issue, **Not Helpful** if they're deflecting
4. **Does the reply contain a commit SHA or link?** → **Helpful** (engineer is showing they applied a fix)

## Diff Verification Logic

### For Suggestion Blocks

1. Extract code between `` ```suggestion `` and `` ``` `` markers
2. Tokenize: keep lines >3 chars, skip punctuation-only lines
3. Compare tokens against `+` (addition) lines in the diff
4. Token match ratio ≥ 50% AND line range overlap → `suggestion-applied`
5. Token match ratio ≥ 50% without line overlap → `suggestion-likely-applied`

### For Prose Comments

1. Get the comment's line range (`start_line` to `line`)
2. Parse diff hunk headers (`@@ -old,count +new,count @@`)
3. Check if any hunk's old-line range overlaps the comment range ±5 lines
4. Overlap found → `exact-lines-modified`

### No-Subsequent-Commits Check

If the commit SHA the comment was left on equals the PR head SHA → `no-subsequent-commits`. This means the PR was merged without any further changes after the review.

## Account Mapping

Engineers have separate personal GitHub accounts and EMU (Enterprise Managed User) accounts. Merge them for per-engineer statistics:

```
personal_login → emu_login → Display Name
```

The mapping is defined in `references/account-map.json` (external JSON file). Update for new team members by editing the JSON directly — no script changes needed.
