---
name: copilot-review-analyst
description: Analyze AI code review effectiveness across Android Auth repositories, including GitHub Copilot in Common/MSAL/Broker and Foundry reviews in the Azure DevOps AuthApp repo. Classify inline findings through human replies and immutable-snapshot verification, then generate one report with clearly labeled GitHub Copilot vs Foundry metrics, per-repo/per-engineer statistics, and trends. Use for "analyze Copilot reviews", "analyze AuthApp reviews", "Foundry review effectiveness", "generate AI review report", "compare Copilot and Foundry", "run review analysis", or any request to measure AI code review quality and adoption.
---

# Copilot Review Analyst

Analyze GitHub Copilot and Foundry review effectiveness in **one combined report**, preserving the review source on every finding. Keep the existing skill name and output filenames for compatibility; title new reports **AI Code Review Effectiveness** and distinguish **GitHub Copilot** from **Foundry** throughout.

## Prerequisites

- **GitHub CLI (`gh`)** authenticated on both `github.com` and `msft.ghe.com`. Select the user's required account for each repository; do not assume one EMU token reaches both hosts.
- **Azure DevOps:** Prefer configured ADO MCP tools scoped to **msazure / One**. The IdentityDivision / Engineering instance cannot read AuthApp; it has no organization override. Do not treat `{}` or `[]` from the wrong organization as an empty AuthApp corpus. If no msazure-scoped tools are available, use the collector's explicitly scoped Azure CLI fallback.
- Read [references/authapp-reviews.md](references/authapp-reviews.md) before collecting or verifying AuthApp reviews.
- **Output directory**: `~/.copilot-review-analysis/` for final artifacts, `$env:TEMP\copilot-review-analysis\` for intermediate data

## Repository Configuration

Default repos (update in scripts if changed):

| Label | Repository | Review source | Platform / account |
|-------|------------|---------------|--------------------|
| common | `AzureAD/microsoft-authentication-library-common-for-android` | GitHub Copilot | github.com / shahzaibj |
| msal | `AzureAD/microsoft-authentication-library-for-android` | GitHub Copilot | github.com / shjameel_microsoft |
| broker | `security/ad-accounts-for-android` | GitHub Copilot | msft.ghe.com / shjameel |
| authapp | `msazure/One/AD-MFA-phonefactor-phoneApp-android` | Foundry | Azure DevOps / organizational access |

Identify Foundry findings by **both** `One Build Service (msazure)` and the **AuthApp PR Reviewer** content marker. The build-service identity is shared with other automation. Never count all comments posted by that identity as AI reviews. Keep provider (`ReviewSource`) separate from hosting platform (`Platform`).

## Analysis Pipeline

The analysis runs in 5 sequential phases. Scripts and templates are bundled in this skill:
- **Scripts:** `scripts/` (preflight, unified collection, AuthApp adapter, verification, classification, history)
- **Assets:** `assets/` (report templates — Markdown, HTML, Outlook HTML)
- **References:** `references/` (classification rules, report formatting guide)

### Phase 1: Data Collection

**Script:** `scripts/analyze.ps1`

Run `scripts/preflight.ps1` first. Collect inline findings from **human-authored PRs merged/completed inside the inclusive start/end date window**, not PR creation dates. AuthApp is included by default; an inaccessible source must block the combined report.

```powershell
# Default: last 60 days
.\.github\skills\copilot-review-analyst\scripts\analyze.ps1

# Custom date range:
.\.github\skills\copilot-review-analyst\scripts\analyze.ps1 -StartDate "2026-01-23"

# Prefer an AuthApp input exported using msazure-scoped ADO MCP tools:
.\.github\skills\copilot-review-analyst\scripts\analyze.ps1 `
    -StartDate "2026-10-01" -EndDate "2026-10-14" `
    -AuthAppInputFile "$env:TEMP\copilot-review-analysis\authapp-export.json"
```

**Parameters:**
- `-StartDate` / `-EndDate` — Inclusive merge/completion window. Format: `YYYY-MM-DD`
- `-OutputDir` — Output directory (default: `$env:TEMP\copilot-review-analysis`)
- `-AuthAppInputFile` — Array of full ADO `{ pullRequest, threads, iterations }` bundles; see the AuthApp reference. Without it, use the explicit msazure Azure CLI fallback.
- `-SkipAuthApp` — Explicit GitHub-only run. Label the report's missing scope; never present it as covering both review sources.

What it does:
1. Fetch merged GitHub PRs and completed AuthApp PRs in the same window, with pagination and explicit access checks.
2. Filter out bot-authored PRs (Copilot, copilot-swe-agent, dependabot, github-actions)
3. For each PR, call `repos/{slug}/pulls/{prNum}/comments` to get inline comments
4. Filter to Copilot comments (user.login = "Copilot") that are top-level (not replies)
5. Find human replies to each Copilot comment (matched via `in_reply_to_id`)
6. For AuthApp, collect full ADO threads and iterations using `analyze-authapp.ps1`. Identify marked reviewer roots, capture later human comments even when their `parentCommentId` is zero, and exclude summary/system/deleted/bot comments from inline denominators.
7. Record `ReviewSource`, `Platform`, full comment/reply text, URLs, and immutable reviewed commits. ADO comment IDs are composite because native comment IDs are only unique within a thread.

**Outputs:**
- `$env:TEMP\copilot-review-analysis\raw_results.json` — all comments with `HasReply` flag and raw reply text. Also records the **force-push fingerprint** per comment: `OriginalCommitId` (the immutable commit Copilot reviewed), `DiffHunk` (frozen copy of the lines Copilot saw), and `ReviewedCommitRewritten` (`true` when the reviewed commit is no longer in the PR's commit list — see [The Force-Push Confound](references/classification-rules.md#the-force-push-confound-mandatory-validation-before-scoring)).
- `$env:TEMP\copilot-review-analysis\review_summaries.json` — PR-level summary comments (for reference)
- `coverage.json` — merged human PRs, reviewed PRs, with-feedback/no-feedback counts by repository, source, and overall. AuthApp summary-only reviews count toward coverage, not inline helpfulness.
- `authapp_*.json` — source-specific collection artifacts, retained for auditing.

### Phase 2: Diff-Level Verification

**Script:** `scripts/precise.ps1`

For every comment with no reply, verify whether the engineer silently acted on the feedback. GitHub uses the compare API; AuthApp uses an available local checkout's immutable reviewed iteration SHA and final source SHA. Missing snapshots remain unknown/unresolved and require ADO verification, never a fabricated negative.

```powershell
.\.github\skills\copilot-review-analyst\scripts\precise.ps1
```

Pass `-OutputDir` for a custom run directory and `-AuthAppRepoPath` if the checkout is elsewhere. The default is the umbrella repo's `AD-MFA-phonefactor-phoneApp-android` folder. This script never fetches or switches branches. For AuthApp LEFT-side threads, do not match target-side line numbers against a source-side diff.

What it does:
1. Load `raw_results.json`, filter to comments where `HasReply = false`
2. For each comment, get the commit SHA it was left on and the PR head SHA
3. Use `repos/{slug}/compare/{commitA}...{commitB}` to get the diff
4. For **suggestion blocks**: extract code tokens, check if they appear as `+` lines in the diff
5. For **prose comments**: check if diff hunk line ranges overlap the comment's line range (±5 line tolerance)

**Verdicts assigned:**
- `suggestion-applied` — suggestion tokens match diff + lines overlap
- `suggestion-likely-applied` — tokens match but lines don't overlap
- `exact-lines-modified` — prose comment's lines were modified
- `lines-modified-different-fix` — nearby lines modified, different code
- `file-changed-elsewhere` — file modified but at different lines
- `file-changed-no-line-info` — file modified but comment had no line number
- `file-not-changed` — file untouched after the comment
- `no-subsequent-commits` — PR merged without any commits after the review

> **Force-push guard:** `precise.ps1` carries `ReviewedCommitRewritten` forward and flags any `not-applied`/`file-not-changed` verdict that sits on a rewritten reviewed snapshot. These are **unsafe to score as Not Helpful** until validated against the immutable `DiffHunk` — the "fix" may live in the orphaned commit. The script prints a `FORCE-PUSH CONFOUND` warning listing them. Resolve each in Phase 3 before finalizing.

**Output:** `$env:TEMP\copilot-review-analysis\precise.json`

### Phase 3: AI Reply Classification

Perform this phase in conversation. Classify **every replied comment from either source** by reading the full finding and engineer reply in context, using the same fairness rubric.

See [references/classification-rules.md](references/classification-rules.md) for detailed guidance on the three-way replied-comment verdict.

**Process:**
1. Load `raw_results.json` and filter to `HasReply -eq true`
2. For each comment, read the `CommentBody` (Copilot's feedback) and `HumanReplyText` (engineer's reply)
3. Classify each reply as `helpful`, `declined`, or `not-helpful` (incorrect). Read the full exchange; do not keyword-match.
   - `helpful`: feedback was accepted, fixed, or acknowledged as actionable
   - `declined`: feedback was correct/reasonable, but the engineer intentionally chose not to act (by-design, subjective, contextual, moot, or out-of-scope). This is neutral and does not count against Copilot.
   - `not-helpful`: the reply demonstrates Copilot was factually or technically wrong. This is the only replied verdict that counts against Copilot quality.
4. **Force-push validation pass (do this before finalizing any `declined`/`not-helpful`/`unresolved`):** for every such comment with `ReviewedCommitRewritten = true`, the engineer rewrote the commit Copilot reviewed — an "outdated / already there / not relevant" dismissal may be hiding a comment that was *correct* at the reviewed snapshot. Re-derive Copilot's claim from the immutable `DiffHunk` (and, if needed, `contents?ref=<OriginalCommitId>`) and compare to the merged state. Flip to `helpful` if the merged code already satisfies the claim; keep `declined` only if declined on the merits; assign `not-helpful` **only** if Copilot was wrong *at `OriginalCommitId`*. Never score a rewritten-snapshot comment as `not-helpful` without that confirmation. Full rule + reproducible `gh` commands in [The Force-Push Confound](references/classification-rules.md#the-force-push-confound-mandatory-validation-before-scoring).
5. Also review `file-changed-elsewhere` and `file-changed-no-line-info` verdicts from Phase 2 to identify re-audit flips
6. For AuthApp, validate the reviewed iteration's `sourceRefCommit` and final code before calling a finding incorrect. `SnapshotNeedsValidation` or a missing snapshot is not proof that Foundry was wrong. ADO `fixed`, `closed`, `wontFix`, or `byDesign` status alone is not a verdict; require reply or code evidence.

**Outputs** (write to `$env:TEMP\copilot-review-analysis\`):
- `reply-verdicts.json` — `{ "commentId": "helpful"|"declined"|"incorrect", ... }` for every replied comment; legacy `"not-helpful"` remains accepted. Preserve AuthApp's entire composite `CommentId` as the key.
- `reaudit-flips.json` — `{ "reauditFlipKeys": ["repo/prNum/filePattern", ...] }` for no-reply comments with strong evidence

See `references/manual-audit-template.json` for the schema.

### Phase 4: Final Classification

**Script:** `scripts/final-classification.ps1`

Merge all results into a single authoritative dataset:

```powershell
.\.github\skills\copilot-review-analyst\scripts\final-classification.ps1 `
    -AccountMapFile ".github\skills\copilot-review-analyst\references\account-map.json" `
    -ReplyVerdictsFile "$env:TEMP\copilot-review-analysis\reply-verdicts.json" `
    -ReauditFlipsFile "$env:TEMP\copilot-review-analysis\reaudit-flips.json"
```

**Parameters:**
- `-OutputDir` — Directory with `raw_results.json` and `precise.json` (default: `$env:TEMP\copilot-review-analysis`)
- `-AccountMapFile` — Path to JSON mapping GitHub logins to display names. See `references/account-map.json`.
- `-ReplyVerdictsFile` — Path to JSON with Phase 3 AI verdicts for replied comments. If omitted, replied comments are "unknown".
- `-ReauditFlipsFile` — Path to JSON with Phase 3 re-audit flips for no-reply comments. If omitted, file-changed-elsewhere defaults to "not-helpful".

What it does:
1. Load `raw_results.json` and `precise.json`
2. Load account mapping, reply verdicts (Phase 3), and re-audit flips (Phase 3)
3. For replied comments: use the AI verdict from `reply-verdicts.json`
4. For no-reply comments: use the Phase 2 diff verdict, with re-audit overrides
5. Preserve source/platform/URL/snapshot fields and produce per-source, per-engineer, and per-repo statistics. Legacy GitHub rows without source fields are interpreted as GitHub Copilot.

**Output:** `$env:TEMP\copilot-review-analysis\final_classification.json`

### Phase 4.5: Append History

**Script:** `scripts/append-history.ps1`

Append a snapshot of the current run to the persistent history file for trend tracking across runs:

```powershell
.\.github\skills\copilot-review-analyst\scripts\append-history.ps1 `
    -PeriodStart "2026-01-24" `
    -PeriodEnd "2026-03-25"
```

**Parameters:**
- `-PeriodStart` — First day of the analysis period (YYYY-MM-DD). This is the `-StartDate` that was passed to `analyze.ps1` in Phase 1. **Required.**
- `-PeriodEnd` — Last day of the analysis period (YYYY-MM-DD). Defaults to today.
- `-InputDir` — Directory with `final_classification.json` and `precise.json` (default: `$env:TEMP\copilot-review-analysis`)
- `-HistoryFile` — Path to persistent history JSON (default: `~/.copilot-review-analysis/history.json`)

**Important:** Periods are **non-overlapping and variable-length**. Each run covers a distinct period — e.g., the first run might cover 60 days, subsequent runs might cover 2 weeks each. The `-PeriodStart` for a new run should be the day after the previous run's `-PeriodEnd`. The script automatically deduplicates entries with the same period.

What it does:
1. Load `final_classification.json` and `precise.json`
2. Compute aggregate stats: response rate, four-way report breakdown, Copilot precision, per-repo, per-engineer
3. Compute normalized volume: `comments/week` for fair comparison across different period lengths
4. Load existing `history.json` (or create empty if first run)
5. Append current snapshot; replace if same period already exists
6. Save sorted by period (newest first)

**Output:** `~/.copilot-review-analysis/history.json`

The history file is an array of snapshots. Each snapshot contains:
- Period metadata: `runDate`, `periodStart`, `periodEnd`, `periodDays`
- Volume: `total`, `commentsPerWeek`, `reviewedPRs`, `avgCommentsPerPR`
- Rates: `responseRate`, `helpful.pct`, `notHelpful.pct`, `unresolved.pct`, `repliedHelpfulRate`
- Source scope and breakdown: `schemaVersion`, `reviewSources`, `repositories`, `sources.{"GitHub Copilot","Foundry"}` with counts, response rate, adoption, precision denominator, and coverage.
- Per-repo breakdown: `repos.{broker,common,msal,authapp}.{comments, responseRate, helpfulPct, ...}`
- Per-engineer breakdown: `engineers.{name}.{comments, responseRate, helpfulPct}`

### Phase 5: Report Generation

Generate both Markdown and Outlook-compatible HTML reports.

Keep all four repositories in the **same report file** for each format/audience. Include an overall **All AI reviews** summary plus a mandatory **Results by Review Source** comparison. Add a **Review source** column to repository, coverage, engineer/source, and example tables. Use exact labels **GitHub Copilot** and **Foundry**; never call mixed totals "Copilot comments" or "Copilot precision". Compute each source's rates from its own denominator.

For old history without source metadata, identify it as **GitHub Copilot only**. Compare like-for-like source/repository scope. Mark Foundry's first period **new baseline**, not an improvement from zero; adding AuthApp is a scope expansion, not a Copilot trend.

**Style/structure references** (in `assets/` — these contain data from the Jan-Mar 2026 analysis and serve as structural templates, NOT to be copied verbatim):
- `assets/Copilot-Code-Review-Effectiveness-Report.md` — Markdown reference (~270 lines, ~3300 words)
- `assets/Copilot-Code-Review-Effectiveness-Report-Outlook.html` — Outlook HTML reference (~345 lines, ~86KB). This is the current **v3 newsletter** design: 1200px hybrid-fluid shell, edge-to-edge newsletter masthead, big-number KPI hero grid, single-row meta-chips, and a Methodology section. It is the **anonymized** sample (uses "Engineer A") and is written single-quoted (escaping-free) for a trivial Graph push.
- `assets/Copilot-Code-Review-Effectiveness-Report.html` — Standard HTML reference

**Important:** The asset templates contain hardcoded numbers (557 comments, specific percentages, engineer names, etc.) from the first analysis. For each new run, generate fresh reports using the same section structure and formatting patterns but with statistics computed from `final_classification.json`.

#### MANDATORY: Read Templates Before Generating

**You MUST read both the Markdown and Outlook HTML template files in full before generating any report.** Do not generate from memory or from the section table in `report-formatting.md` alone — the templates contain critical patterns that are not captured in the section list:

- **Narrative prose paragraphs** between every table explaining the significance of the data (not just "here's a table")
- **Callout boxes** (blue for insights, yellow for warnings) that frame key findings
- **Background section** with team context and Copilot enablement framing
- **"At a Glance" summary cards** with a detailed adoption callout underneath
- **Scope metric cards** (Human PRs, PRs reviewed, Total comments, Avg per PR)
- **Full Copilot comment text** in examples (not truncated snippets)
- **Explanatory paragraphs after examples** describing why the comment was helpful/unhelpful
- **Detailed Recommendations section** with prose paragraphs (not bullet points)
- **Bar chart visualizations** (response rate bar, helpfulness verdict bar, per-repo bars) in HTML

#### Quality Gate

After generating each report, compare its dimensions against the template:
- **Markdown:** Must be ≥250 lines and ≥3000 words. If significantly smaller, the report is missing narrative depth.
- **Outlook HTML:** Must be ≥300 lines and ≥70KB. If significantly smaller, the report is missing visual elements or prose. (The v3 newsletter design is single-quoted and layout-dense, so it runs fewer lines but far more KB than the old `<font>`-based template.)

If a report doesn't meet these thresholds, re-read the template and identify what's missing before saving.

#### v3 Newsletter Design & Delivery (Outlook)

- **Headline leads with adoption.** The hero band and the first Key Takeaway lead with **Helpful & adopted %** (e.g. "78.3% of comments adopted"), with precision as the supporting second number — never lead with precision (it is near-100% by construction and overstates the story).
- **Load-bearing render techniques** live in [references/report-formatting.md](references/report-formatting.md) § *v3 Newsletter Rendering*: single-row meta-chip table (not `inline-block` tables — Outlook stacks them), `mso-line-height-rule:exactly` on body prose, edge-to-edge frame (whiten `bgcolor` **and** inline CSS), and the inset masthead + `1120px` VML width. Apply the edge-to-edge/masthead transforms report-scoped — never edit the shared shell.
- **Deliver as a color-preserving draft**, not paste. Preferred: Graph draft (`workiq-create_entity` on `/me/messages`). Fallback when WorkIQ/Graph is unavailable: Outlook COM (`Outlook.Application` → set `.HTMLBody` → `.Save()`/`.Display()`) with an ASCII-safe subject.

**Generate two versions of each report:**
1. **Team-internal** — uses real engineer names (from account map). For the team.
2. **Org-wide** — anonymizes engineers as "Engineer A", "Engineer B", etc., sorted by helpfulness descending. For sharing outside the team.

**Process:**
1. Load `final_classification.json`
2. **Read full template files** (`assets/Copilot-Code-Review-Effectiveness-Report.md` and `assets/Copilot-Code-Review-Effectiveness-Report-Outlook.html`) — do NOT skip this step
3. Compute aggregate statistics (total, per-source, per-repo, per-engineer/source, canonical verdict breakdown, precision). Display unknown counts if manual classification is unfinished; use **n/a** when a precision denominator is zero.
4. Load `raw_results.json` to collect full comment text for helpful, declined, and genuinely incorrect examples
5. Load `~/.copilot-review-analysis/history.json` for trend data. If ≥2 entries exist, generate a **Trend** section comparing the current run with the previous run(s). See [references/report-formatting.md](references/report-formatting.md) for trend section formatting rules.
6. Generate reports matching the template's section structure, narrative depth, and visual formatting
7. **Verify dimensions** (word count, line count) against the quality gate thresholds
8. Save to `~/.copilot-review-analysis/`:
   - `Copilot-Code-Review-Effectiveness-Report.md` (team, real names)
   - `Copilot-Code-Review-Effectiveness-Report-Anonymous.md` (org-wide)
   - `Copilot-Code-Review-Effectiveness-Report-Outlook.html` (team, real names)
   - `Copilot-Code-Review-Effectiveness-Report-Outlook-Anonymous.html` (org-wide)

See [references/report-formatting.md](references/report-formatting.md) for the report structure and Outlook HTML formatting rules.

## Key Principle: "Unresolved" ≠ "Not Helpful"

The report must present four distinct outcome buckets:
- **Confirmed Helpful** = positive evidence (explicit acknowledgment OR verified fix in diff)
- **Declined** = Copilot was correct/reasonable, but the engineer intentionally chose not to act. This is neutral.
- **Incorrect** = a replied comment where the engineer demonstrated that Copilot was factually or technically wrong
- **Unresolved** = insufficient evidence either way (engineer never engaged)

Comments with no reply and no diff evidence are **Unresolved**, not assumed incorrect. `final-classification.ps1` emits canonical `helpful`, `declined`, `incorrect`, `unresolved`, and `unknown` values. When reading older datasets only, split legacy `not-helpful` by `Replied`.

Report **Review precision** as `Helpful / (Helpful + Incorrect)` overall and per source. Exclude Declined, Unresolved, and Unknown from both numerator and denominator. Use **GitHub Copilot precision** or **Foundry precision** only for the respective source.

**Compute precision only after the force-push validation pass.** A comment on a rewritten snapshot can be a correct comment masquerading as a false positive, and it lands directly in the precision denominator. Before finalizing precision, resolve every `ReviewedCommitRewritten = true` comment per [The Force-Push Confound](references/classification-rules.md#the-force-push-confound-mandatory-validation-before-scoring), and report the count of rewritten-snapshot comments as a measurement-integrity note so readers know the denominator was validated.

## Copilot Comment Identification

- Copilot inline review comments use `user.login = "Copilot"`
- Legacy bot: `copilot-pull-request-reviewer[bot]`
- Bot PR authors to exclude: `app/copilot-swe-agent`, `dependabot[bot]`, `github-actions[bot]`
- Only count top-level comments (`in_reply_to_id` is null/0), not Copilot's own replies

## Rate Limiting

The scripts call the GitHub API heavily. Built-in mitigations:
- PR comment caching (fetched once per PR)
- Diff caching (fetched once per commit range)
- Sleep every 15 PRs (300ms) and every 25 diff checks (200ms)
- Use `--paginate` for repos with many comments

If hitting rate limits, increase sleep intervals or use `GH_TOKEN` with higher rate limits.
