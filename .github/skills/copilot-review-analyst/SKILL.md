---
name: copilot-review-analyst
description: Analyze GitHub Copilot code review effectiveness across Android Auth repositories. Collects all Copilot inline review comments via GitHub API, classifies them as helpful/not-helpful/unresolved through reply analysis, diff verification, and AI-assisted classification, then generates a report with per-repo and per-engineer statistics. Use this skill when asked to "analyze Copilot reviews", "measure Copilot review effectiveness", "generate Copilot review report", "how helpful are Copilot reviews", "run review analysis", or any request to measure/report on GitHub Copilot code review quality and adoption.
---

# Copilot Review Analyst

Analyze GitHub Copilot code review effectiveness across the Android Auth repositories by collecting all inline review comments, classifying each one, and producing a comprehensive report.

## Prerequisites

- **GitHub CLI (`gh`)** authenticated with an **EMU account** (`*_microsoft`) which has access to all three repos (public and private). The script auto-detects the EMU account from `gh auth status`.
- The `analyze.ps1` script automatically switches to the EMU account at startup and restores the original account on completion — no manual auth switching needed
- **Output directory**: `~/.copilot-review-analysis/` for final artifacts, `$env:TEMP\copilot-review-analysis\` for intermediate data

## Repository Configuration

Default repos (update in scripts if changed):

| Label | Slug | Auth |
|-------|------|------|
| common | `AzureAD/microsoft-authentication-library-common-for-android` | EMU (also accessible via personal) |
| msal | `AzureAD/microsoft-authentication-library-for-android` | EMU (also accessible via personal) |
| broker | `identity-authnz-teams/ad-accounts-for-android` | EMU only |

## Analysis Pipeline

The analysis runs in 5 sequential phases. Scripts and templates are bundled in this skill:
- **Scripts:** `scripts/` (3 core pipeline scripts)
- **Assets:** `assets/` (report templates — Markdown, HTML, Outlook HTML)
- **References:** `references/` (classification rules, report formatting guide)

### Phase 1: Data Collection

**Script:** `scripts/analyze.ps1`

Collect all Copilot inline review comments from human-authored PRs:

```powershell
# Default: last 60 days
.\.github\skills\copilot-review-analyst\scripts\analyze.ps1

# Custom date range:
.\.github\skills\copilot-review-analyst\scripts\analyze.ps1 -StartDate "2026-01-23"
```

**Parameters:**
- `-StartDate` — Start date for PR search (default: 60 days ago). Format: `YYYY-MM-DD`
- `-OutputDir` — Output directory (default: `$env:TEMP\copilot-review-analysis`)

What it does:
1. Fetch all PRs created after `-StartDate` via `gh pr list`
2. Filter out bot-authored PRs (Copilot, copilot-swe-agent, dependabot, github-actions)
3. For each PR, call `repos/{slug}/pulls/{prNum}/comments` to get inline comments
4. Filter to Copilot comments (user.login = "Copilot") that are top-level (not replies)
5. Find human replies to each Copilot comment (matched via `in_reply_to_id`)
6. Record whether each comment has a reply and capture the reply text (no classification at this stage)

**Outputs:**
- `$env:TEMP\copilot-review-analysis\raw_results.json` — all comments with `HasReply` flag and raw reply text
- `$env:TEMP\copilot-review-analysis\review_summaries.json` — PR-level summary comments (for reference)

### Phase 2: Diff-Level Verification

**Script:** `scripts/precise.ps1`

For every comment with no reply, verify whether the engineer silently acted on the feedback:

```powershell
.\.github\skills\copilot-review-analyst\scripts\precise.ps1
```

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

**Output:** `$env:TEMP\copilot-review-analysis\precise.json`

### Phase 3: AI Reply Classification

This phase is performed by the agent (you) in conversation. Classify **every replied comment** by reading the full Copilot comment and engineer reply in context.

See [references/classification-rules.md](references/classification-rules.md) for detailed guidance on what counts as helpful vs not-helpful.

**Process:**
1. Load `raw_results.json` and filter to `HasReply -eq true`
2. For each comment, read the `CommentBody` (Copilot's feedback) and `HumanReplyText` (engineer's reply)
3. Classify as `helpful` or `not-helpful` based on the engineer's intent (read the full reply, don't keyword-match)
4. Also review `file-changed-elsewhere` and `file-changed-no-line-info` verdicts from Phase 2 to identify re-audit flips

**Outputs** (write to `$env:TEMP\copilot-review-analysis\`):
- `reply-verdicts.json` — `{ "commentId": "helpful"|"not-helpful", ... }` for every replied comment
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
5. Produce per-engineer and per-repo statistics

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
2. Compute aggregate stats: response rate, three-way breakdown, per-repo, per-engineer
3. Compute normalized volume: `comments/week` for fair comparison across different period lengths
4. Load existing `history.json` (or create empty if first run)
5. Append current snapshot; replace if same period already exists
6. Save sorted by period (newest first)

**Output:** `~/.copilot-review-analysis/history.json`

The history file is an array of snapshots. Each snapshot contains:
- Period metadata: `runDate`, `periodStart`, `periodEnd`, `periodDays`
- Volume: `total`, `commentsPerWeek`, `reviewedPRs`, `avgCommentsPerPR`
- Rates: `responseRate`, `helpful.pct`, `notHelpful.pct`, `unresolved.pct`, `repliedHelpfulRate`
- Per-repo breakdown: `repos.{broker,common,msal}.{comments, responseRate, helpfulPct, ...}`
- Per-engineer breakdown: `engineers.{name}.{comments, responseRate, helpfulPct}`

### Phase 5: Report Generation

Generate both Markdown and Outlook-compatible HTML reports.

**Style/structure references** (in `assets/` — these contain data from the Jan-Mar 2026 analysis and serve as structural templates, NOT to be copied verbatim):
- `assets/Copilot-Code-Review-Effectiveness-Report.md` — Markdown reference (~270 lines, ~3300 words)
- `assets/Copilot-Code-Review-Effectiveness-Report-Outlook.html` — Outlook HTML reference (~430 lines, ~42KB)
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
- **Outlook HTML:** Must be ≥400 lines and ≥35KB. If significantly smaller, the report is missing visual elements or prose.

If a report doesn't meet these thresholds, re-read the template and identify what's missing before saving.

**Generate two versions of each report:**
1. **Team-internal** — uses real engineer names (from account map). For the team.
2. **Org-wide** — anonymizes engineers as "Engineer A", "Engineer B", etc., sorted by helpfulness descending. For sharing outside the team.

**Process:**
1. Load `final_classification.json`
2. **Read full template files** (`assets/Copilot-Code-Review-Effectiveness-Report.md` and `assets/Copilot-Code-Review-Effectiveness-Report-Outlook.html`) — do NOT skip this step
3. Compute aggregate statistics (total, per-repo, per-engineer, three-way breakdown)
4. Load `raw_results.json` to collect full comment text for 4-5 helpful and 4-5 unhelpful examples
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

Comments with no reply and no diff evidence are **Unresolved**, not assumed unhelpful. This is a critical distinction:
- **Confirmed Helpful** = positive evidence (explicit acknowledgment OR verified fix in diff)
- **Confirmed Not Helpful** = positive evidence (explicit dismissal with stated reason OR comment on stale code)
- **Unresolved** = insufficient evidence either way (engineer never engaged)

The `final-classification.ps1` script classifies no-response/no-diff-evidence comments as "not-helpful" for conservative stats, but the report should present the three-way breakdown to be honest about uncertainty.

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
