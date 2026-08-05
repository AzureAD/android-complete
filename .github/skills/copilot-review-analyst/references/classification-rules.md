# Classification Rules

Guide for the AI agent performing Phase 3 reply classification. Read each replied comment's full context (Copilot's comment + engineer's reply) and assign a verdict.

## Phase 3: Classifying Replied Comments

For every comment where `HasReply = true`, read the `CommentBody` (what Copilot said) and `HumanReplyText` (what the engineer replied), then assign one of **three** verdicts:

- **`helpful`** — The engineer's reply indicates Copilot's feedback led to (or will lead to) a code improvement
- **`not-helpful`** — Copilot's feedback was **factually wrong** (a false positive, hallucination, or a demonstrable error about the code/library/framework). This is the only bucket that counts *against* Copilot's review quality.
- **`declined`** — Copilot's feedback was **correct or reasonable**, but the engineer **intentionally chose not to act** on it for a by-design, subjective, or contextual reason (or the code became moot). This is **neutral** — it is *not* counted against Copilot, because Copilot had no way to know the author's intent beforehand.

### Why `declined` exists (the fairness principle)

A dismissal is **not** evidence that Copilot was wrong. Copilot reviews a diff without the author's design intent, offline discussions, or downstream context. When an engineer replies "intentional" or "this is fine as-is," Copilot's observation was often perfectly valid — the engineer simply made a judgment call. Counting these against Copilot punishes it for not being a mind-reader and understates real review quality.

So we split the old "not-helpful" bucket into two:
- **`not-helpful` (incorrect)** — Copilot was actually *wrong*. Fair to count against it.
- **`declined` (by-design / subjective / moot)** — Copilot was *right or reasonable*, engineer declined. Neutral.

The **Copilot precision** metric measures correctness only where it is evaluable:

> **precision = helpful ÷ (helpful + not-helpful-incorrect)**

`declined` and `unresolved` are excluded from both numerator and denominator. Precision answers: *"When Copilot spoke and we could judge correctness, how often was it actually right?"*

### What counts as Helpful

- **Explicit acknowledgment**: "good catch", "fixed", "done", "addressed", "will fix", "thanks", "agreed", "makes sense", "you're right", "great catch", etc.
- **Action taken**: "added unit test", "refactored", "renamed", "removed", "reverted", "pushed a fix", "committed"
- **Delegated back to Copilot**: Reply contains `@copilot` asking it to apply the fix
- **Indirect confirmation**: "addressing in a later commit", "implemented something similar", "I did switch the ordering"
- **Linked a commit**: Reply contains a commit SHA or link showing they applied a fix
- **Acknowledged for future**: "this can be considered in another PR" (acknowledges the issue is valid)

### What counts as Not Helpful (Copilot incorrect)

Reserve this bucket for cases where the engineer's reply demonstrates Copilot was **factually wrong**:

- **Disproven claim**: Engineer verified the opposite — "Verified this isn't reproducible on Mockito 5.11.0; the suite passes 12/12" (Copilot was wrong about library behavior)
- **Hallucination**: Copilot flagged something that isn't there — "The `@Override` annotation is already there" (Copilot claimed it was missing)
- **Wrong about the code/framework/version**: Copilot's premise is objectively false for this codebase
- **False positive with proof**: Engineer shows the flagged issue cannot occur

If the engineer *demonstrates* Copilot got a fact wrong → `not-helpful`.

### What counts as Declined (neutral — Copilot correct/reasonable, engineer declined)

- **By-design**: "intentional", "by design", "this is intentional", "we consciously chose this", "keeping as-is"
- **Subjective / nitpick**: "too nitpicky", "the current name is fine", "minor, no-fix", "imo this reads fine"
- **Contextual reason to keep**: "we need this log to catch a regression in cobo/cope", "for a new feature we want the full stack trace"
- **Acceptable risk**: "this should be okay since it's in a log file"
- **Moot / obviated**: "not relevant now that we removed this change", "outdated" (the code changed so the comment no longer applies — Copilot wasn't *wrong*, just stale)
- **Deprecated target**: "this config is deprecated, not fixing"
- **Accurate but declined**: engineer explicitly says the comment *was* accurate but chose not to act ("this is outdated, but was accurate")

The test: *Did the engineer demonstrate Copilot was wrong (→ not-helpful), or did they acknowledge/ignore a valid-but-unwanted point (→ declined)?* When in doubt between `not-helpful` and `declined`, prefer **`declined`** unless there is positive evidence Copilot was factually incorrect.

### Edge Cases

- **Mixed signals** (both positive and negative in same reply): Read the full reply to determine the engineer's overall intent. Don't rely on individual words — understand the sentence.
- **Administrative replies** ("will consider later", "not for this PR"): Classify as **helpful** if they acknowledge the issue is valid but defer it; classify as **declined** if they're setting it aside as by-design/out-of-scope (not because Copilot was wrong).
- **Short/ambiguous replies** ("ok", "noted", "see above"): Use the Copilot comment context to infer intent. If it reads as acknowledgment → helpful. If it reads as a soft dismissal of a valid point → declined. Only use **not-helpful** when there is evidence Copilot was factually wrong.
- **"Outdated"**: Treat as **declined** (the code moved on; not a Copilot correctness failure) unless the engineer indicates Copilot's underlying claim was itself wrong. **Before scoring, apply the Force-Push Confound check below** — an "outdated" marker on a rewritten snapshot can hide a comment that was *correct* when Copilot reviewed it, and must never be recorded as `not-helpful`.

### Important: Read the Full Reply

Do NOT use simple keyword matching. Read the engineer's full reply in context. For example:
- "This won't fix the actual issue we're seeing" — This is NOT a "won't fix" dismissal; the engineer is discussing a different topic
- "Thanks but this is intentional" — Despite "thanks", this is a **declined** (by-design), not a Copilot error
- "I disagree with this specific suggestion but good catch on the typo above" — Mixed; classify based on the primary concern
- "~ = approximate, I think this is ok" — Copilot's readability nit was reasonable; engineer made a judgment call → **declined**, not not-helpful

## The Force-Push Confound (mandatory validation before scoring)

**A "correct" Copilot comment can look like a false positive when the engineer rewrites history.** If an engineer rebases or force-pushes *after* Copilot reviews — a common habit right after opening a PR — the commit Copilot reviewed is orphaned. GitHub then marks the comment **"outdated"** and the visible code no longer matches what Copilot saw, so a comment that was *accurate at the reviewed snapshot* reads as stale or even hallucinated. Left unchecked, this silently inflates the `declined`/`unresolved` buckets and, in the worst case, scores a **correct** comment as `not-helpful` — understating precision.

> This was not hypothetical: it caused the single decisive misclassification in the Jul 2026 audit (broker #212's `@Override` comment was mislabeled a hallucination; the `@Override` was genuinely absent at the reviewed commit `bcf360ea`, which the engineer force-pushed away ~2 min later). Precision moved **up** once corrected.

### Detection — three immutable fields

Phase 1 (`analyze.ps1`) now records these per comment in `raw_results.json`:

- **`OriginalCommitId`** — `original_commit_id`, the exact commit Copilot reviewed. Immutable; survives force-push.
- **`ReviewedCommitRewritten`** — `true` when `OriginalCommitId` is **absent** from the PR's current commit list (`/pulls/{n}/commits`), i.e. the reviewed snapshot was rewritten. This is the force-push fingerprint.
- **`DiffHunk`** — `diff_hunk`, a frozen copy of the exact lines Copilot saw. Ground truth for what Copilot was actually looking at.

`precise.ps1` carries `ReviewedCommitRewritten` forward and flags any `not-applied`/`file-not-changed` verdict that sits on a rewritten snapshot.

### The rule

For **every** comment classified `declined`, `not-helpful`, `unresolved`, or a no-reply `not-applied`/`file-not-changed` verdict, **first check `ReviewedCommitRewritten`**:

1. If `false` → classify normally (no confound).
2. If `true` → the dismissal is **unsafe to credit as-is**. Re-derive Copilot's claim from the immutable `DiffHunk` (and, if needed, the reviewed file via `contents?ref=<OriginalCommitId>`) and compare it to the **merged** state:
   - Copilot's claim was **true at the reviewed snapshot** and the merged code already satisfies it → the fix was effectively adopted by the rewrite → **helpful** (never `not-helpful`, never `declined`).
   - Copilot's claim was **true at the reviewed snapshot** and the engineer explicitly declined it on the merits ("intentional", "okay in a log file") → **declined** (the rewrite was incidental).
   - Copilot's claim was **false even at the reviewed snapshot** → `not-helpful` (a genuine error).

**Never assign `not-helpful` to a comment on a rewritten snapshot without confirming Copilot was wrong at `OriginalCommitId`.** A stale marker is not evidence of a Copilot error.

### Precision is computed only after this validation

Because a mis-scored force-push case lands directly in the precision denominator, **precision must be recomputed only after the force-push validation pass has run** over all `declined`/`not-helpful`/`unresolved` comments. Report the count of `ReviewedCommitRewritten = true` comments as a measurement-integrity note.

### Reproducible check (per comment)

```bash
# 1. The commit Copilot reviewed (immutable):
gh api repos/<slug>/pulls/comments/<commentId> --jq '.original_commit_id'
# 2. Is it still in the PR's commit list? (empty output => rewritten away => confound)
gh api repos/<slug>/pulls/<pr>/commits --paginate --jq '.[].sha' | grep <original_commit_id>
# 3. Ground truth Copilot saw:
gh api repos/<slug>/pulls/comments/<commentId> --jq '.diff_hunk'
# 4. (optional) The reviewed file as Copilot saw it:
gh api "repos/<slug>/contents/<path>?ref=<original_commit_id>" --jq '.content' | base64 -d
```

## Phase 3 Output Format

Write two JSON files to `$env:TEMP\copilot-review-analysis\`:

### `reply-verdicts.json`

Map of comment ID to verdict for every replied comment:

```json
{
    "1234567890": "helpful",
    "1234567891": "not-helpful",
    "1234567892": "declined"
}
```

Keys are comment IDs (as strings). Values are `"helpful"`, `"not-helpful"` (Copilot incorrect), or `"declined"` (Copilot correct/reasonable but engineer declined).

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
| `file-not-changed`, `no-subsequent-commits`, `not-applied` | **Not Helpful** (unless in re-audit flips) |

> **Force-push caveat:** any `not-applied` / `file-not-changed` row with `ReviewedCommitRewritten = true` is UNSAFE to score as Not Helpful — the reviewed snapshot was rewritten, so the fix may live in the orphaned commit. Validate against the immutable `DiffHunk` (see "The Force-Push Confound") before finalizing; flip to **Helpful** if Copilot's claim was already satisfied by the merged code.

## Account Mapping

Engineers have separate personal GitHub accounts and EMU (Enterprise Managed User) accounts. Merge them for per-engineer statistics.

The mapping is defined in `references/account-map.json` (external JSON file). Update for new team members by editing the JSON directly — no script changes needed.
