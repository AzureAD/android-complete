# Report Formatting Guide

Rules for generating Copilot Code Review Effectiveness reports in Markdown and Outlook-compatible HTML.

## Report Structure

Generate both formats. Templates are in `assets/` within this skill folder.

**CRITICAL:** The section table below lists *what* each section covers, but not *how deep* each section should be. Always read the full asset templates to understand the expected narrative depth. The templates contain 3000+ words of prose — not just tables and bullet points.

| # | Section | Content | Depth |
|---|---------|---------|-------|
| 1 | **Background** | Team context, what repos are covered, what was enabled | 2-3 prose paragraphs |
| 2 | **At a Glance** | 5 summary cards (helpful %, declined %, incorrect %, unresolved %, precision) + callout explaining the fairness correction | Cards + 1 detailed callout box |
| 3 | **Overall Results** | Response rate bar, four-way verdict bar, precision formula, breakdown tables | Narrative paragraph before each visual + verdict definitions table + yellow warning callout + breakdown tables |
| 4 | **Results by Repository** | Per-repo bars + table (comments, response rate, helpful/declined/incorrect/unresolved/precision) | Bar per repo + data table + 1 interpretive paragraph |
| 5 | **Results by Engineer** | Table with colored columns (anonymize names for org-wide sharing) | Full table + blue callout box highlighting the engagement-value correlation |
| 6 | **Response Behavior Deep Dive** | What happens to ignored comments (silently applied, merged without commits, etc.) | Summary stats + detailed breakdown table + interpretive paragraph |
| 7 | **What Copilot Is Good At** | 4-5 real examples with PR references and engineer quotes | Each example: category header + full Copilot comment text (not truncated) + engineer reply + 1-2 sentence explanation |
| 8 | **When Copilot Was Wrong / When Feedback Was Declined** | Genuine errors and correct-but-declined examples in separate subsections | Full quotes + explanatory context; never present a deliberate decline as a Copilot error |
| 9 | **Most Reviewed Files** | Top 10 files by comment count | Table + 1 interpretive paragraph |
| 10 | **Key Takeaways** | 7-8 numbered findings | Each finding: bold stat + explanatory sentence |
| 11 | **Recommendations** | 3 actionable next steps | Each recommendation: 1 full prose paragraph (not a bullet point) with reasoning |
| 12 | **Methodology Notes** | How data was collected, classified, and validated | 5-6 bullet points with sufficient detail for reproducibility |

**Headline framing (all formats).** The hero band and the first Key Takeaway lead with **Helpful & adopted %** (adoption), *not* precision. Adoption answers "did engineers act on the feedback?" — the question leadership cares about — and precision is presented as the supporting second number. Leading with precision (which is near-100% by construction once Declined/Unresolved are excluded) overstates the story and buries the adoption signal.

## Trend Section (Section 2.5 — between At a Glance and Overall Results)

**Only generated when `history.json` has ≥2 entries.** Skip entirely on the first run.

### Data Source

Load `~/.copilot-review-analysis/history.json`. Entries are sorted newest-first.

### Comparison Rules

Since periods may have different lengths (e.g., 60 days vs 14 days):

1. **Compare rates/percentages, not absolute counts.** Response rate, helpful %, combined dismissal %, unresolved %, and replied-helpful rate are directly comparable across any period length. For snapshots created after the three-way methodology change, also show Declined %, Incorrect %, and Precision.
2. **Show counts as context only.** Display total comments alongside `comments/week` for normalized volume comparison. Do NOT compute count deltas like "comments dropped from 570 to 85" — this is misleading when periods differ.
3. **Show period duration prominently.** Every trend row must include the date range and duration (e.g., "Jan 24–Mar 25 (60d)").
4. **Use "pp" (percentage points) for deltas.** "↑ +7.6pp" not "↑ +7.6%". The delta is the arithmetic difference between two percentages.
5. **Color-code deltas.** Green (↑) for improvements (response rate up, helpful up, not-helpful down, unresolved down). Red (↓) for regressions.
6. **Preserve trend continuity.** Historical snapshots created before the `declined` verdict only have a combined dismissal rate. Label that column **Dismissed (combined)** and explain that the current period can be split into Declined (neutral) and Incorrect (counts against Copilot). Do not retroactively infer the split for old periods.

### Markdown Format (2 runs — current vs previous)

```markdown
## Trend: This Run vs Previous

| Metric | Previous (Jan 24–Mar 25, 60d) | Current (Mar 25–Apr 8, 14d) | Delta |
|--------|-------------------------------|------------------------------|-------|
| Comments | 570 (66.3/wk) | 85 (42.5/wk) | — |
| Response rate | 44.4% | 52.0% | **↑ +7.6pp** |
| Helpful | 38.6% | 45.0% | **↑ +6.4pp** |
| Not helpful | 17.4% | 15.0% | ↑ -2.4pp |
| Unresolved | 44.0% | 40.0% | **↑ -4.0pp** |
| Replied helpful rate | 60.9% | 65.0% | **↑ +4.1pp** |
```

Add a 1-2 sentence narrative below the table interpreting the direction (e.g., "Response rate improved by 7.6 percentage points, suggesting engineers are engaging more with Copilot reviews since the team discussion. However, the data covers only 14 days — we'll need another cycle to confirm the trend.")

### Markdown Format (3+ runs — full history table)

```markdown
## Historical Trend

| Period | Duration | Comments | Cmt/wk | Response Rate | Helpful | Not Helpful | Unresolved |
|--------|----------|----------|--------|---------------|---------|-------------|------------|
| Mar 25–Apr 8 | 14d | 85 | 42.5 | **52.0%** | **45.0%** | 15.0% | 40.0% |
| Jan 24–Mar 25 | 60d | 570 | 66.3 | 44.4% | 38.6% | 17.4% | 44.0% |
```

Bold the most recent row. Add an interpretive paragraph after the table.

### Outlook HTML Format

Use the same table-based approach as the rest of the report:

- **Delta cells:** Green background (`#dafbe1`) for improvements, red background (`#ffebe9`) for regressions
- **Arrow indicators:** `&#9650;` (▲) for positive, `&#9660;` (▼) for negative
- **Per-run bars:** Same horizontal bar technique as per-repo bars — one row per historical run showing helpful/not-helpful/unresolved as percentage-width colored cells

### What Counts as Improvement

| Metric | Improvement | Regression |
|--------|-------------|------------|
| Response rate | ↑ (higher) | ↓ (lower) |
| Helpful % | ↑ (higher) | ↓ (lower) |
| Not helpful % | ↓ (lower) | ↑ (higher) |
| Unresolved % | ↓ (lower) | ↑ (higher) |
| Replied helpful rate | ↑ (higher) | ↓ (lower) |
| Comments/week | Neutral — show but don't color |

## Statistics to Compute

From `final_classification.json`:

```powershell
# Overall
$total = $data.Count
$helpful = ($data | Where-Object { $_.Verdict -eq "helpful" }).Count
$declined = ($data | Where-Object { $_.Verdict -eq "declined" }).Count
$incorrect = ($data | Where-Object {
    $_.Replied -eq $true -and $_.Verdict -eq "not-helpful"
}).Count
$unresolved = ($data | Where-Object {
    $_.Replied -eq $false -and $_.Verdict -eq "not-helpful"
}).Count
$replied = ($data | Where-Object { $_.Replied -eq $true }).Count
$responseRate = [math]::Round(($replied / $total) * 100, 1)
$precisionDenominator = $helpful + $incorrect
$precision = if ($precisionDenominator -gt 0) {
    [math]::Round(($helpful / $precisionDenominator) * 100, 1)
} else { 0 }

# Per-repo
$repoStats = $data | Group-Object Repo | ForEach-Object { ... }

# Per-engineer
$engStats = $data | Group-Object Engineer | ForEach-Object { ... }
```

## Outlook HTML Formatting Rules

> **Canonical design:** `assets/Copilot-Code-Review-Effectiveness-Report-Outlook.html` is the current **v3 newsletter** template — read it in full before generating. The primitives below (table layout, inline styles, `bgcolor`+inline pairing, bars-not-SVG) still hold; the load-bearing v3 techniques (single-row chips, edge-to-edge frame, newsletter masthead, `mso-line-height-rule`) are documented under **v3 Newsletter Rendering** below. The asset is written entirely single-quoted so it stays escaping-free (`"`/`\` count = 0) for a trivial Graph push.

Outlook strips most modern CSS. Follow these rules strictly:

### Layout
- The current shell is a **1200px hybrid-fluid** newsletter (VML hero fallback + stacking media query), not a fixed centered table. Older reports used a centered `<table width="1000">`; the v3 shell supersedes it. Do not hand-roll the shell — reuse the asset's outer structure.
- Use **table-based layouts only** — no flexbox, no grid, no float
- All styles must be **inline** — Outlook strips `<style>` blocks entirely

### Headings
Use a table with colored background instead of `<h1>`–`<h3>`:
```html
<table cellpadding="0" cellspacing="0" border="0" width="100%"
       style="margin:0 0 14px;">
  <tr>
    <td style="background:#c8e1ff;border-left:5px solid #0969da;
               padding:10px 16px;">
      <font size="4" face="Segoe UI,Helvetica,Arial,sans-serif">
        <b>Section Title</b>
      </font>
    </td>
  </tr>
</table>
```

### Summary Cards
Use a 5-column `<table>` with nested tables per card:
```html
<td width="20%" style="padding:6px;">
  <table style="border:1px solid #d0d7de;border-left:4px solid #COLOR;">
    <tr><td style="padding:14px;text-align:center;">
      <div style="font-size:30px;font-weight:700;color:#COLOR;">VALUE</div>
      <div style="font-size:12px;color:#656d76;">label</div>
    </td></tr>
  </table>
</td>
```

Card border colors: `#2da44e` (green/helpful), `#0969da` (blue/declined-neutral), `#cf222e` (red/incorrect), `#bf8700` (yellow/unresolved), `#8250df` (purple/precision).

### Bar Charts
Use table with percentage-width cells and background colors:
```html
<table width="100%" style="border-collapse:collapse;">
  <tr>
    <td width="41%" style="background:#2da44e;padding:6px;color:#fff;font-size:12px;">
      Helpful 41%
    </td>
    <td width="20%" style="background:#0969da;padding:6px;color:#fff;font-size:12px;">
      Declined 20%
    </td>
    <td width="2%" style="background:#cf222e;padding:6px;color:#fff;font-size:12px;">
      Incorrect 2%
    </td>
    <td width="37%" style="background:#bf8700;padding:6px;color:#fff;font-size:12px;">
      Unresolved 37%
    </td>
  </tr>
</table>
```

### Data Tables
```html
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="border-collapse:collapse;font-size:13px;">
  <tr>
    <td style="background:#f6f8fa;padding:8px 12px;border:1px solid #d0d7de;
               font-weight:600;">Header</td>
  </tr>
  <tr>
    <td style="padding:8px 12px;border:1px solid #d0d7de;">Data</td>
  </tr>
</table>
```

### Colored Columns (per-engineer table)
Apply cell backgrounds for visual encoding:
- Green: `background:#dafbe1` (helpful)
- Blue: `background:#ddf4ff` (declined — neutral)
- Red: `background:#ffebe9` (incorrect)
- Yellow: `background:#fff8c5` (unresolved)

## Verdict Presentation Rules

Every report generated under the three-way reply methodology must present four final outcome buckets:

1. **Helpful** — positive evidence that the feedback was accepted or silently applied.
2. **Declined** — Copilot was correct or reasonable, but the engineer intentionally chose not to act. This is neutral and must never use red styling or be described as low-quality feedback.
3. **Incorrect** — a replied comment where the engineer demonstrated that Copilot was factually or technically wrong. This is the only bucket that counts against Copilot quality.
4. **Unresolved** — no reply and no definitive diff evidence. This is unknown, not incorrect.

Show **Copilot Precision** prominently:

> `Precision = Helpful / (Helpful + Incorrect)`

Exclude Declined and Unresolved from both the numerator and denominator. In prose, explicitly distinguish adoption (Helpful), intentional judgment calls (Declined), correctness failures (Incorrect), and missing evidence (Unresolved).

### Legends
Use a nested table with colored cells instead of unicode squares:
```html
<table cellpadding="0" cellspacing="0" border="0" style="display:inline-table;">
  <tr>
    <td style="background:#2da44e;width:12px;height:12px;">&nbsp;</td>
    <td style="padding:0 8px 0 4px;font-size:12px;">Helpful</td>
  </tr>
</table>
```

### Callout Boxes
```html
<table width="100%" style="margin:16px 0;">
  <tr>
    <td style="background:#ddf4ff;border-left:4px solid #0969da;
               padding:14px 18px;font-size:14px;">
      <strong>Key insight header.</strong> Body text here.
    </td>
  </tr>
</table>
```

### What Outlook Strips
- CSS `color` on text elements (use `<font color>` sparingly)
- `<h1>`–`<h3>` styling
- `<style>` blocks entirely
- Flexbox, grid, float
- CSS variables
- `border-radius` (degrades gracefully)

### What Outlook Preserves
- `background` on `<td>`
- `<font size>` and `<font face>`
- Table widths (px and %)
- `<b>`, `<strong>`, `<em>`
- Inline `style` attributes
- `border-left`, `border` on cells
- `padding`, `margin` on cells

## v3 Newsletter Rendering (Outlook) — load-bearing learnings

These were hardened over ~20 render/deliver iterations. Each fixes a defect that looked fine in a browser preview but broke in the classic-Outlook Word engine. When something renders wrong in the mailbox, the cause is almost always here.

### Meta chips must be ONE single-row table
The header meta-chips (period, comments, repos, etc.) must be a **single** `<table><tr>` with each chip as a `<td>` and thin `<td width='7'>&nbsp;</td>` spacers between them. Add `white-space:nowrap` to each chip cell.

- **Do NOT** render each chip as its own `display:inline-block` `<table>`. Classic Outlook's Word engine ignores `display:inline-block` on a `<table>` and treats every table as block-level, so the chips **stack vertically** in Outlook even though they sit horizontally in a browser.

### Body prose needs `mso-line-height-rule:exactly`
Paragraph text looked cramped in Outlook because the Word engine ignores a plain `line-height`. On every body `<p>`/prose cell set **both** `line-height:23px;mso-line-height-rule:exactly;`. Without the `mso-` rule the lines collapse to the font's default leading.

### Edge-to-edge frame (report-scoped transform, Outlook only)
The default shell has an `#eef1f6` page background + outer gutter + card border/shadow (the grey "frame"). For the emailed newsletter we whiten it edge-to-edge. This is a **report-scoped post-process**, applied to the generated HTML only — never edit the shared skeleton.

1. Whiten the `<body>` background: change the inline `background-color` to `#ffffff`.
2. Whiten the outer canvas `<td>`/`<table>` — you **must** change **both** the `bgcolor` attribute **and** the inline `background-color`. The Word engine reads `bgcolor`; whitening only the inline CSS leaves the grey frame in Outlook even though the browser preview looks clean.
3. Zero the outer gutter: `padding:32px 28px` → `padding:0`.
4. Strip the container `border`, `border-radius`, and `box-shadow`.

### Newsletter masthead (inset hero + VML width)
So the blue hero band reads as a newsletter masthead (aligned to the text column, not wider than the body):

1. Inset the hero cell to the text column, e.g. `padding:24px 40px 0 40px`.
2. Set the VML fallback width = **container − 2×gutter** (1200 − 2×40 = **1120px**). The VML rectangle stays square; matching its width to the inset makes classic Outlook fill the band to the same column as everything else.
3. Round the hero `<div>` with `border-radius:10px`. It rounds in New Outlook/browser and squares off in classic Outlook — clean graceful degradation, no broken corners.

### Delivery — color-preserving draft, with a COM fallback
Deliver as a **draft**, never as paste (Outlook's paste sanitizer flattens inline text color).

- **Preferred:** Graph draft via `workiq-create_entity` on `/me/messages` (`201 Created` = draft in mailbox, opens in New Outlook with colors intact). Iterate on the same draft with `workiq-update_entity`.
- **Fallback (WorkIQ/Graph unavailable):** Outlook COM —
  ```powershell
  $ol = New-Object -ComObject Outlook.Application
  $mail = $ol.CreateItem(0)           # olMailItem
  $mail.Subject = $subject            # ASCII-safe: plain hyphens, no en/em-dash
  $mail.HTMLBody = $html
  $mail.Save(); $mail.Display()
  ```
  Delete duplicate test drafts by exact subject match before re-saving. The single-quoted asset embeds directly with zero escaping.

### Subject line — recurring prefix + catchy hook
Stable prefix so it threads/filters, plus a punchy metric hook, e.g.
`Copilot Review - Android Auth - 78.3% of comments adopted, 99.1% precision (Cycle 4)`.
Keep it ASCII (plain hyphens) so it survives COM.

## Engineer Anonymization

Generate **two versions** of every report:

| Version | Engineer Names | File Suffix | Audience |
|---------|---------------|-------------|----------|
| Team-internal | Real names from account map | *(none)* | Team members |
| Org-wide | "Engineer A", "Engineer B", etc. | `-Anonymous` | Leadership, other teams |

Anonymization rules for the org-wide version:
- Sort engineers by helpfulness rate descending, then assign letters (A = highest)
- Replace names in the per-engineer table, example quotes, and any narrative mentions
- Keep repo names visible (Common, MSAL, Broker) — these are not sensitive
- PR numbers may be kept (they're meaningless without repo access)
