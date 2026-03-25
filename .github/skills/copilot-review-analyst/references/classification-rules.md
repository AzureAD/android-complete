# Classification Rules

Guide for the AI agent performing Phase 3 reply classification. Read each replied comment's full context (Copilot's comment + engineer's reply) and assign a verdict.

## Phase 3: Classifying Replied Comments

For every comment where `HasReply = true`, read the `CommentBody` (what Copilot said) and `HumanReplyText` (what the engineer replied), then assign one of:

- **`helpful`** — The engineer's reply indicates Copilot's feedback led to (or will lead to) a code improvement
- **`not-helpful`** — The engineer's reply indicates Copilot's feedback was wrong, irrelevant, or not actionable

### What counts as Helpful

- **Explicit acknowledgment**: "good catch", "fixed", "done", "addressed", "will fix", "thanks", "agreed", "makes sense", "you're right", "great catch", etc.
- **Action taken**: "added unit test", "refactored", "renamed", "removed", "reverted", "pushed a fix", "committed"
- **Delegated back to Copilot**: Reply contains `@copilot` asking it to apply the fix
- **Indirect confirmation**: "addressing in a later commit", "implemented something similar", "I did switch the ordering"
- **Linked a commit**: Reply contains a commit SHA or link showing they applied a fix
- **Acknowledged for future**: "this can be considered in another PR" (acknowledges the issue is valid)

### What counts as Not Helpful

- **Explicit dismissal**: "won't fix", "by design", "intentional", "not applicable", "false positive", "not relevant"
- **Copilot was wrong**: "incorrect", "Copilot is wrong", "hallucinating", "not accurate", "misunderstanding"
- **Already handled**: "already done", "already handled", "this is fine"
- **Explained away**: Engineer explains why the suggestion doesn't apply — "this is just telemetry", "we consciously chose this", "legacy code", "overdo", "only used in X context", "can't happen"
- **Dismissed or outdated**: "outdated", "dismissed", "out of scope"

### Edge Cases

- **Mixed signals** (both positive and negative in same reply): Read the full reply to determine the engineer's overall intent. Don't rely on individual words — understand the sentence.
- **Administrative replies** ("will consider later", "not for this PR"): Classify as **helpful** if they acknowledge the issue is valid but defer it; classify as **not-helpful** if they're brushing it off.
- **Short/ambiguous replies** ("ok", "noted", "see above"): Use the Copilot comment context to infer whether the engineer is acknowledging or dismissing. When genuinely unclear, lean toward **not-helpful** (conservative).

### Important: Read the Full Reply

Do NOT use simple keyword matching. Read the engineer's full reply in context. For example:
- "This won't fix the actual issue we're seeing" — This is NOT a "won't fix" dismissal; the engineer is discussing a different topic
- "Thanks but this is intentional" — Despite "thanks", this is a dismissal
- "I disagree with this specific suggestion but good catch on the typo above" — Mixed; classify based on the primary concern

## Phase 3 Output Format

Write two JSON files to `$env:TEMP\copilot-review-analysis\`:

### `reply-verdicts.json`

Map of comment ID to verdict for every replied comment:

```json
{
    "1234567890": "helpful",
    "1234567891": "not-helpful",
    "1234567892": "helpful"
}
```

Keys are comment IDs (as strings). Values are `"helpful"` or `"not-helpful"`.

### `reaudit-flips.json`

For no-reply comments where Phase 2 returned `file-changed-elsewhere` or `file-changed-no-line-info`, review the Copilot comment and diff evidence to decide if the fix was applied differently. Record any that should flip to helpful:

```json
{
    "reauditFlipKeys": [
        "common/3027/AzureActiveDirectory.java",
        "broker/94/TelemetryRegionSupplier"
    ]
}
```

Format: `"repo/prNumber/partialFilePath"`. Only include entries with strong evidence.

## No-Reply Comments (Phase 2 Diff Verdicts)

These are handled by `precise.ps1` and `final-classification.ps1` automatically:

| Diff Verdict | Final Classification |
|-------------|---------------------|
| `suggestion-applied`, `suggestion-likely-applied`, `exact-lines-modified` | **Helpful** |
| `lines-modified-different-fix` | **Helpful** |
| `file-changed-elsewhere`, `file-changed-no-line-info` | **Not Helpful** (unless in re-audit flips) |
| `file-not-changed`, `no-subsequent-commits`, `not-applied` | **Not Helpful** |

## Account Mapping

Engineers have separate personal GitHub accounts and EMU (Enterprise Managed User) accounts. Merge them for per-engineer statistics.

The mapping is defined in `references/account-map.json` (external JSON file). Update for new team members by editing the JSON directly — no script changes needed.
