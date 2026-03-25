# Report Formatting Guide

Rules for generating Copilot Code Review Effectiveness reports in Markdown and Outlook-compatible HTML.

## Report Structure

Generate both formats. Templates are in `assets/` within this skill folder.

**CRITICAL:** The section table below lists *what* each section covers, but not *how deep* each section should be. Always read the full asset templates to understand the expected narrative depth. The templates contain 3000+ words of prose — not just tables and bullet points.

| # | Section | Content | Depth |
|---|---------|---------|-------|
| 1 | **Background** | Team context, what repos are covered, what was enabled | 2-3 prose paragraphs |
| 2 | **At a Glance** | 4 summary cards (no-response %, helpful %, not-helpful %, unresolved %) + callout about adoption | Cards + 1 detailed callout box |
| 3 | **Overall Results** | Response rate bar, helpfulness verdict bar, breakdown tables | Narrative paragraph before each visual + verdict definitions table + yellow warning callout + 2 breakdown tables |
| 4 | **Results by Repository** | Per-repo bars + table (comments, response rate, helpful/not/unresolved) | Bar per repo + data table + 1 interpretive paragraph |
| 5 | **Results by Engineer** | Table with colored columns (anonymize names for org-wide sharing) | Full table + blue callout box highlighting the engagement-value correlation |
| 6 | **Response Behavior Deep Dive** | What happens to ignored comments (silently applied, merged without commits, etc.) | Summary stats + detailed breakdown table + interpretive paragraph |
| 7 | **What Copilot Is Good At** | 4-5 real examples with PR references and engineer quotes | Each example: category header + full Copilot comment text (not truncated) + engineer reply + 1-2 sentence explanation |
| 8 | **What Copilot Struggles With** | 4-5 real examples showing false positives, domain gaps | Same format as above — full quotes + explanatory context |
| 9 | **Most Reviewed Files** | Top 10 files by comment count | Table + 1 interpretive paragraph |
| 10 | **Key Takeaways** | 7-8 numbered findings | Each finding: bold stat + explanatory sentence |
| 11 | **Recommendations** | 3 actionable next steps | Each recommendation: 1 full prose paragraph (not a bullet point) with reasoning |
| 12 | **Methodology Notes** | How data was collected, classified, and validated | 5-6 bullet points with sufficient detail for reproducibility |

## Statistics to Compute

From `final_classification.json`:

```powershell
# Overall
$total = $data.Count
$helpful = ($data | Where-Object { $_.Verdict -eq "helpful" }).Count
$notHelpful = ($data | Where-Object { $_.Verdict -eq "not-helpful" }).Count
$unresolved = $total - $helpful - $notHelpful
$replied = ($data | Where-Object { $_.Replied -eq $true }).Count
$responseRate = [math]::Round(($replied / $total) * 100, 1)

# Per-repo
$repoStats = $data | Group-Object Repo | ForEach-Object { ... }

# Per-engineer
$engStats = $data | Group-Object Engineer | ForEach-Object { ... }
```

## Outlook HTML Formatting Rules

Outlook strips most modern CSS. Follow these rules strictly:

### Layout
- Wrap entire body in a centered `<table width="1000">` for consistent margins
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
Use a 4-column `<table>` with nested tables per card:
```html
<td width="25%" style="padding:6px;">
  <table style="border:1px solid #d0d7de;border-left:4px solid #COLOR;">
    <tr><td style="padding:14px;text-align:center;">
      <div style="font-size:30px;font-weight:700;color:#COLOR;">VALUE</div>
      <div style="font-size:12px;color:#656d76;">label</div>
    </td></tr>
  </table>
</td>
```

Card border colors: `#656d76` (gray/neutral), `#2da44e` (green/helpful), `#cf222e` (red/not helpful), `#bf8700` (yellow/unresolved).

### Bar Charts
Use table with percentage-width cells and background colors:
```html
<table width="100%" style="border-collapse:collapse;">
  <tr>
    <td width="41%" style="background:#2da44e;padding:6px;color:#fff;font-size:12px;">
      Helpful 41%
    </td>
    <td width="18%" style="background:#cf222e;padding:6px;color:#fff;font-size:12px;">
      Not helpful 18%
    </td>
    <td width="41%" style="background:#bf8700;padding:6px;color:#fff;font-size:12px;">
      Unresolved 41%
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
- Red: `background:#ffebe9` (not helpful)
- Yellow: `background:#fff8c5` (unresolved)

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
