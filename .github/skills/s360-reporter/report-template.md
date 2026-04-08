# S360 Report HTML Template Reference

This file documents the HTML building blocks for generating the S360 weekly report.
The report is Outlook-compatible (uses tables for layout, `bgcolor` for colors, no CSS classes).

## Design System

### Color Palette

| Purpose | Hex | Usage |
|---------|-----|-------|
| Primary accent | `#0078d4` | Header bar, blue pill, program underlines, info callout |
| Out of SLA | `#cf222e` | Cards, badges, left borders, row tint `#fff5f5` / `#fef2f2` |
| Approaching SLA | `#e65100` | Badges, left borders, row tint `#fff8f0` / `#fffbeb` |
| In SLA | `#2e7d32` | Badges, left borders, card tint `#f0fdf4` / `#e8f5e9` |
| Missing ETA | `#bf8700` | Badge bg, card tint `#fffbeb`, text color for warnings |
| Table header bg | `#e8edf2` | Column headers |
| Table header border | `#d0d7de` | Column header borders |
| Table cell border | `#e8e8e8` | All data cell borders |
| Zebra row | `#fafafa` | Alternating data rows |
| Body text | `#1a1a1a` | Headings |
| Link text | `#24292f` | Table title links |
| Link blue | `#0078d4` | PBI links, URL links |
| Resolved green | `#2da44e` | Resolved callout left bar |
| Muted text | `#656d76` | Subtle text |

### Border Radius Values

| Element | Radius |
|---------|--------|
| Main container | `12px` |
| Summary stat cards | `12px` |
| Severity bar | `6px` |
| Out of SLA card | `10px` |
| SLA/ETA badge pills | `4px` |
| Blue date pill (header) | `16px` |
| PBI chip (in card) | `16px` |
| MISSED SLA badge (in card) | `12px` |

---

## Building Blocks

### 1. Page Wrapper

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>S360 Weekly Report — {{DATE}}</title>
</head>
<body style="margin:0; padding:0; background-color:#f0f2f5; font-family:'Segoe UI', Arial, sans-serif; line-height:1.5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f0f2f5;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="960" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff; border-radius:12px; border:1px solid #d0d0d0;">

  <!-- Content goes here -->

</table>
</td></tr>
</table>
</body>
</html>
```

### 2. Header

Blue top bar + uppercase label + team name + blue date pill + services line.

```html
<tr><td>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td bgcolor="#0078d4" style="height:6px; font-size:1px; line-height:1px; border-radius:12px 12px 0 0;">&nbsp;</td></tr>
  </table>
</td></tr>
<tr>
  <td style="padding:36px 56px 0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td valign="middle">
          <p style="margin:0 0 2px 0; font-size:13px; font-weight:600; text-transform:uppercase; letter-spacing:1.5px; color:#0078d4;">S360 Weekly Report</p>
          <h1 style="margin:0 0 4px 0; font-size:28px; color:#1a1a1a; font-weight:700;">Android Auth Team</h1>
        </td>
        <td width="200" valign="middle" align="right">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td bgcolor="#0078d4" style="padding:8px 20px; color:#ffffff; font-size:12px; font-weight:600; border-radius:16px;">
              Week of {{SHORT_DATE}}
            </td>
          </tr></table>
        </td>
      </tr>
    </table>
    <p style="margin:16px 0 0 0; font-size:13px;">
      Services: <strong>AuthN SDK - MSAL Android</strong> &bull; <strong>AuthN SDK - ADAL Android</strong> &bull; <strong>Microsoft Authenticator - Android</strong>
    </p>
  </td>
</tr>
```

### 3. Summary Stat Card

One card. Repeat 5x with different colors/values.

```html
<td width="165" valign="top">
  <table cellpadding="0" cellspacing="0" border="0" width="165" bgcolor="{{CARD_BG}}" style="border:1px solid {{CARD_BORDER}}; border-top:3px solid {{CARD_ACCENT}}; border-radius:12px;">
  <tr><td style="padding:20px 16px; text-align:center; height:56px;" valign="middle">
    <p style="margin:0; font-size:34px; font-weight:800; color:{{CARD_ACCENT}};">{{COUNT}}</p>
    <p style="margin:6px 0 0 0; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;"><em>{{LABEL}}</em></p>
  </td></tr>
  </table>
</td>
```

Card configs:

| Card | CARD_BG | CARD_BORDER | CARD_ACCENT |
|------|---------|-------------|-------------|
| Total | (none) | `#e1e4e8` | `#0078d4` |
| Out of SLA | `#fef2f2` | `#fca5a5` | `#cf222e` |
| Approaching | `#fffbeb` | `#fcd34d` | `#e65100` |
| In SLA | `#f0fdf4` | `#86efac` | `#2e7d32` |
| No ETA | `#fffbeb` | `#fcd34d` | `#bf8700` |

### 4. Severity Bar

```html
<tr>
  <td style="padding:20px 56px 0 56px;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="height:28px; border-radius:6px; overflow:hidden;">
      <tr>
        <td width="{{OUT_PCT}}%" bgcolor="#cf222e" style="color:#fff; font-size:11px; font-weight:700; text-align:center; padding:4px 0;">{{OUT_COUNT}}</td>
        <td width="{{NEAR_PCT}}%" bgcolor="#e65100" style="color:#fff; font-size:11px; font-weight:700; text-align:center; padding:4px 0;">{{NEAR_COUNT}}</td>
        <td width="{{IN_PCT}}%" bgcolor="#2e7d32" style="color:#fff; font-size:11px; font-weight:700; text-align:center; padding:4px 0;">{{IN_COUNT}} In SLA</td>
      </tr>
    </table>
  </td>
</tr>
```

### 5. Section Divider

```html
<tr><td style="padding:28px 56px 0 56px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td bgcolor="#e8e8e8" style="height:1px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
  </table>
</td></tr>
```

### 6. Resolved Since Last Week Callout

```html
<tr>
  <td style="padding:28px 56px 0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="4" bgcolor="#2da44e" style="font-size:1px;">&nbsp;</td>
        <td bgcolor="#e8f5e9" style="padding:16px 20px;">
          <p style="margin:0 0 8px 0; font-size:14px; font-weight:700;">&#x2705; Resolved Since Last Week</p>
          <p style="margin:0; font-size:13px; line-height:1.6;">
            <!-- For each resolved item: -->
            <b>{{TITLE}}</b> &mdash; <a href="{{PBI_URL}}" style="color:#0078d4;">AB#{{PBI_ID}}</a> &mdash; {{ASSIGNEE}} &mdash; <b>Done</b><br>
            <!-- If no resolved items: -->
            <em>No resolved items were detected this week.</em>
          </p>
        </td>
      </tr>
    </table>
  </td>
</tr>
```

### 7. Needs Attention Header + Out of SLA Card

Only shown if there are Out of SLA items. Render one card per Out of SLA item.

```html
<tr>
  <td style="padding:28px 56px 0 56px;">
    <h3 style="margin:0 0 4px 0; font-size:16px; color:#1a1a1a; font-weight:700;">Needs Attention</h3>
    <p style="margin:0 0 0 0; font-size:12px;"><em>Items past due or approaching their SLA deadline</em></p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 14px 0;">
      <tr><td bgcolor="#cf222e" style="height:3px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
    </table>

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:2px solid #cf222e; border-radius:10px; overflow:hidden;">
      <tr>
        <td bgcolor="#fef2f2" style="padding:20px 24px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td valign="top" width="65%">
                <p style="margin:0 0 8px 0; font-size:16px; font-weight:700;">
                  <a href="{{S360_URL}}" style="color:#1a1a1a; text-decoration:none;">{{TITLE}}</a>
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
                  <tr>
                    <td bgcolor="#cf222e" style="padding:3px 10px; color:#fff; font-size:10px; font-weight:bold; border-radius:12px;">MISSED SLA</td>
                    <td style="padding:0 0 0 8px; font-size:12px;"><em>{{SERVICE_NAME}}</em></td>
                  </tr>
                </table>
                <p style="margin:0; font-size:13px;"><b>Due:</b> <b style="color:#cf222e;">{{DUE_DATE}}</b> ({{DAYS_OVERDUE}} days overdue)</p>
                <p style="margin:4px 0 0 0; font-size:13px;"><b>ETA:</b> {{ETA_DISPLAY}}</p>
                <p style="margin:4px 0 0 0; font-size:13px;"><b>Owner:</b> {{OWNER_DISPLAY}}</p>
              </td>
              <td valign="top" width="35%" align="right">
                <table cellpadding="0" cellspacing="0" border="0">
                  <tr><td style="padding:6px 14px; font-size:13px; border:1px solid #d0d7de; border-radius:16px; background:#fff;">
                    <a href="{{PBI_URL}}" style="color:#0078d4; text-decoration:none; font-weight:600;">AB#{{PBI_ID}}</a> {{NEW_BADGE}}
                  </td></tr>
                </table>
                <p style="margin:10px 0 0 0; font-size:12px;"><em>{{PROGRAM}} &bull; {{SUBTYPE}}</em></p>
                <p style="margin:4px 0 0 0; font-size:12px;"><em>Wave: {{WAVE}}</em></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td>
</tr>
```

### 8. Program Section Header

```html
<tr>
  <td style="padding:28px 56px 0 56px;">
    <h3 style="margin:0 0 4px 0; font-size:16px; color:#1a1a1a; font-weight:700;">{{PROGRAM_NAME}}</h3>
    <p style="margin:0 0 0 0; font-size:12px;"><em>{{PROGRAM_DESCRIPTION}} &mdash; {{ITEM_COUNT}} item(s)</em></p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 14px 0;">
      <tr><td bgcolor="#0078d4" style="height:3px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
    </table>
```

### 9. Program Table Column Headers

```html
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="28%">Title</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="12%">Service</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="14%">Owner</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="8%">SLA</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="9%">Due</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="10%">ETA</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;" width="19%">PBI</td>
      </tr>
```

### 10. Table Data Row

Row styling varies by SLA state and zebra stripe position.

**SLA-based styling:**

| SLA State | Row BG (odd) | Row BG (even) | Left Border | Badge BG | Badge Color |
|-----------|-------------|---------------|-------------|----------|-------------|
| Missed | `#fff5f5` | `#fff5f5` | `#cf222e` | `#cf222e` | white |
| Near | `#fff8f0` | `#fff8f0` | `#e65100` | `#e65100` | white |
| In SLA | (none) | `#fafafa` | `#2e7d32` | `#e8f5e9` | `#2e7d32` |

```html
      <tr>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:13px; border:1px solid #e8e8e8; border-left:4px solid {{LEFT_BORDER_COLOR}};">
          <a href="{{S360_URL}}" style="color:#24292f; font-weight:600; text-decoration:none;">{{TITLE}}</a><br>
          <span style="font-size:11px;"><em>{{SUBTITLE}}</em></span>
        </td>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">{{SERVICE}}</td>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">{{OWNER_NAME}}<br><span style="font-size:11px;"><em>({{OWNER_ALIAS}})</em></span></td>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="{{BADGE_BG}}" style="padding:2px 8px; color:{{BADGE_COLOR}}; font-size:10px; font-weight:bold; border-radius:4px;">{{SLA_LABEL}}</td></tr></table>
        </td>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">{{DUE_SHORT}}</td>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">{{ETA_CELL}}</td>
        <td bgcolor="{{ROW_BG}}" style="padding:12px 14px; font-size:12px; border:1px solid #e8e8e8;">{{PBI_CELL}}</td>
      </tr>
```

**ETA cell variants:**
- Has ETA: plain text date, e.g. `May 4`
- Missing ETA: `<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#bf8700" style="padding:2px 8px; font-size:10px; font-weight:600; color:#fff; border-radius:4px;">No ETA</td></tr></table>`

**PBI cell variants:**
- Existing: `<a href="{{URL}}" style="color:#0078d4; font-weight:600; text-decoration:none;">AB#{{ID}}</a>`
- New: same but append ` &#x1F195;`
- None: `<em>None</em>`

### 11. Program Section Close

```html
    </table>
  </td>
</tr>
```

### 12. Ownership Breakdown Table

```html
<tr>
  <td style="padding:28px 56px 0 56px;">
    <h2 style="margin:0 0 16px 0; font-size:16px; color:#1a1a1a; border-bottom:2px solid #0078d4; padding-bottom:8px;">&#128101; Ownership Breakdown</h2>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de;">Assignee</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">Total</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">&#128308;</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">&#128992;</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">&#128994;</td>
        <td bgcolor="#e8edf2" style="padding:10px 14px; font-size:11px; font-weight:700; color:#2d3748; text-transform:uppercase; letter-spacing:0.5px; border:1px solid #d0d7de; text-align:center;" width="80">No ETA</td>
      </tr>
      <!-- Repeat per assignee: -->
      <tr>
        <td bgcolor="{{ZEBRA_BG}}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8;">{{NAME}} <span style="font-size:11px;"><em>({{ALIAS}})</em></span></td>
        <td bgcolor="{{ZEBRA_BG}}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center;"><b>{{TOTAL}}</b></td>
        <td bgcolor="{{OUT_CELL_BG}}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center; {{OUT_STYLE}}">{{OUT_COUNT}}</td>
        <td bgcolor="{{NEAR_CELL_BG}}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center; {{NEAR_STYLE}}">{{NEAR_COUNT}}</td>
        <td bgcolor="{{ZEBRA_BG}}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center;">{{IN_COUNT}}</td>
        <td bgcolor="{{ZEBRA_BG}}" style="padding:10px 14px; font-size:13px; border:1px solid #e8e8e8; text-align:center; {{ETA_STYLE}}">{{NO_ETA_COUNT}}</td>
      </tr>
    </table>
  </td>
</tr>
```

**Ownership cell highlighting:**
- Out of SLA count > 0: `bgcolor="#fef2f2"`, `font-weight:700; color:#cf222e;`
- Near SLA count > 0: `bgcolor="#fffbeb"`, `font-weight:600;`
- No ETA count > 0: `font-weight:600; color:#bf8700;`

### 13. Action Required Callout

Three variants — one per callout type. Build by repeating this pattern:

```html
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;">
  <tr>
    <td width="4" bgcolor="{{BAR_COLOR}}" style="font-size:1px;">&nbsp;</td>
    <td bgcolor="{{BG_COLOR}}" style="padding:16px 20px;">
      <p style="margin:0 0 6px 0; font-size:14px; font-weight:700;">{{HEADING}}</p>
      <p style="margin:0; font-size:13px;">{{CONTENT}}</p>
    </td>
  </tr>
</table>
```

| Callout | BAR_COLOR | BG_COLOR | Heading |
|---------|-----------|----------|---------|
| Needs owners | `#cf222e` | `#fef2f2` | "4 Items Need Owners" |
| Missing ETA | `#bf8700` | `#fffbeb` | "8 Items Missing ETA" |
| New PBIs | `#0078d4` | `#ddf4ff` | "14 New PBIs Created" |

### 14. Footer

```html
<tr>
  <td style="padding:32px 56px 40px 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td bgcolor="#e8e8e8" style="height:1px; font-size:1px; line-height:1px;">&nbsp;</td></tr>
    </table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;">
      <tr>
        <td valign="middle"><p style="margin:0; font-size:11px;"><em>Auto-generated by S360 Reporter &bull; {{DATE}}</em></p></td>
        <td valign="middle" align="right"><p style="margin:0; font-size:11px;">
          <a href="https://s360.msftcloudes.com" style="color:#0078d4; text-decoration:none;">S360 Dashboard</a> &bull;
          <a href="https://dev.azure.com/IdentityDivision/Engineering/_queries/query/" style="color:#0078d4; text-decoration:none;">ADO Board</a>
        </p></td>
      </tr>
    </table>
  </td>
</tr>
```

---

## Report Assembly Order

1. Page wrapper open
2. Header (block 2)
3. Summary cards row (block 3 × 5, wrapped in a `<tr><td>` with `cellspacing="6"`)
4. Severity bar (block 4)
5. Divider (block 5)
6. Resolved callout (block 6)
7. Needs Attention header + Out of SLA cards (block 7) — only if Out of SLA items exist
8. Divider (block 5)
9. For each program (sorted by worst SLA first):
   - Program header (block 8)
   - Column headers (block 9)
   - Data rows (block 10 × N, with SLA-based styling)
   - Section close (block 11)
10. Divider (block 5)
11. Ownership table (block 12)
12. Divider (block 5)
13. Action Required section header + callouts (block 13 × 3)
14. Footer (block 14)
15. Page wrapper close

## Program Categorization

Derive program name directly from S360 API fields (no heuristic mapping needed):

**Primary**: Use `CustomDimensions.S360_WavesMetadata[0].ProgramDisplayName` (e.g., "IDNA Governed SFI Work Items", "Azure SDL").

**Refinement**: If `ProgramDisplayName` is too generic (e.g., multiple items share the same program),
further group by `CustomDimensions.filter` (e.g., "Threat Model Review", "CodeQL") or
`CustomDimensions.campaign` (e.g., "CFS Adoption").

**Person-targeted items** (e.g., on-call checklists) have no `ProgramDisplayName`. Group these
by `CustomDimensions.TeamName` or use a fallback label like "On-Call Readiness".

**Section header content**:
- `h3` title = program/filter name (e.g., "Continuous SDL", "CFS Pipeline Onboarding")
- Subtitle = descriptive text derived from the items + item count

Programs are ordered by their worst SLA state: programs containing Missed items first,
then programs with Near items, then all-In-SLA programs. Within same SLA tier, order by
earliest due date.
