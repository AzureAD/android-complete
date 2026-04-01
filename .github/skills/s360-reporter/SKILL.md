---
name: s360-reporter
description: Generate S360 weekly reports for the Android Auth team. Fetches active action items from S360 MCP server, creates ADO work items (PBIs) for untracked items, and produces a polished Outlook-compatible HTML email report. Triggers include "S360 report", "generate S360 report", "weekly S360", "S360 status", "what are our S360 items", or any request to review, report on, or triage S360 action items for the Android Auth team.
---

# S360 Weekly Report Generator

Generate a polished S360 weekly report for the Android Auth team. Fetches live data from
the S360 MCP server, ensures every item has an ADO PBI, and produces an Outlook-compatible
HTML email report.

## Prerequisites

- **S360 MCP Server** must be running (configured in `.vscode/mcp.json` as `s360-breeze-mcp`)
- **ADO MCP Server** must be running (for PBI creation)
- Read the **Outlook HTML report prompt** at `{{VSCODE_USER_PROMPTS_FOLDER}}/outlook-html-report.prompt.md`
  for HTML rendering rules before generating the report

## Target Services

| Service | Service Tree ID |
|---------|----------------|
| AuthN SDK - ADAL Android | `937cdc57-1253-4b55-878e-5854368926a2` |
| AuthN SDK - MSAL Android | `8d0d308e-cd5c-44a3-9518-43eeeb424b57` |
| Microsoft Authenticator - Android | `0b97f26e-fcfc-4ed1-95e9-1dca3a2fde3b` |

## Workflow

### Step 1: Fetch S360 Data

Call `mcp_s360-breeze-m_search_active_s360_kpi_action_items` with all three target IDs:

```
request: {
  "pageSize": 50,
  "targetIds": [
    "937cdc57-1253-4b55-878e-5854368926a2",
    "8d0d308e-cd5c-44a3-9518-43eeeb424b57",
    "0b97f26e-fcfc-4ed1-95e9-1dca3a2fde3b"
  ]
}
```

If more than 50 items, paginate using the `nextCursor` field.

### Step 2: Parse and Deduplicate Items

The response contains an array at `result.resources`. For each item extract:

| Field | JSON Path | Notes |
|-------|-----------|-------|
| Title | `Title` | |
| Service | Map `TargetId` → service name from table above |
| Owner Alias | `S360Dimensions.ActionOwnerAlias` | Falls back to `AssignedTo` |
| Owner Name | `S360Dimensions.ActionOwner` | |
| Due Date | `CurrentDueDate` | Format as `Mon DD, YYYY` |
| SLA State | `SLAState` | Values: `OutOfSla`, `ApproachingSla`, `InSla` |
| ETA | `CurrentETA` | If null → flag as **"Missing ETA ⚠"** |
| Status Notes | `CurrentStatus` | May be empty |
| Status Author | `CurrentStatusAuthor` | |
| ADO Work Item | `S360Dimensions.ADOWorkItemHTMLUrl` | Empty = no PBI linked |
| S360 URL | `URL` | Link to details/remediation |
| KPI ID | `KpiId` | For dedup |
| Action Item ID | `KpiActionItemId` | For dedup |
| Initiative | `CustomDimensions.initiative` | JSON array string |
| Wave | Extract from `CustomDimensions.S360_WavesMetadata[0].WaveDisplayName` |

**Dedup**: Some items appear twice with different `KpiActionItemId` but same `Title` and
`TargetId`. Group by `Title` + `TargetId` and merge, keeping the one with worst SLA state.

### Step 3: Ensure ADO PBIs Exist

For each item where `S360Dimensions.ADOWorkItemHTMLUrl` is empty:

1. **Ask the user** if they want PBIs auto-created for untracked items.
2. If yes, for each untracked item use `mcp_ado_wit_create_work_item`:
   ```json
   {
     "project": "Engineering",
     "workItemType": "Product Backlog Item",
     "fields": [
       {"name": "System.Title", "value": "[S360] <item Title>"},
       {"name": "System.Description", "value": "<HTML description with S360 details, URL, and due date>", "format": "Html"},
       {"name": "System.AreaPath", "value": "<discovered from user's recent items>"},
       {"name": "System.IterationPath", "value": "<current iteration>"},
       {"name": "System.AssignedTo", "value": "<OwnerAlias>@microsoft.com"},
       {"name": "Microsoft.VSTS.Common.Priority", "value": "<1 for OutOfSla, 2 for ApproachingSla, 3 for InSla>"},
       {"name": "System.Tags", "value": "s360; ai-generated"}
     ]
   }
   ```
3. Follow the `pbi-creator` skill's Step 2 for ADO defaults discovery (area path, iteration)
   — but batch the question, don't ask per item.
4. Record created PBI IDs for the report.

### Step 4: Generate HTML Report

Read the Outlook HTML report prompt at `{{VSCODE_USER_PROMPTS_FOLDER}}/outlook-html-report.prompt.md`
to follow all rendering rules.

**Report structure:**

1. **Header** — "S360 Weekly Report — {date}" with subtitle "Android Auth Team"
2. **Summary banner** — Total items, Out of SLA count, Approaching SLA count, In SLA count,
   Missing ETA count. Use colored stat cards.
3. **🔴 Out of SLA section** — Table of items past due. Columns: #, Title, Service, Owner,
   Due Date, ETA, Notes, PBI Link. Use red-tinted rows.
4. **🟠 Approaching SLA section** — Same table format. Orange-tinted rows.
5. **🟢 In SLA section** — Same table format. Standard rows.
6. **By Assignee breakdown** — Table: Assignee, Total, Out of SLA, Approaching, In SLA.
   Sorted by severity (most out-of-SLA first).
7. **Items Missing ETA** — Callout box listing items with no ETA set.
8. **Items Without ADO PBI** — Callout box listing items with no linked work item (or newly
   created PBIs with links).
9. **Footer** — "Auto-generated by S360 Reporter skill" + link to S360 dashboard + date.

**Display conventions:**
- SLA State badges: `OutOfSla` → red badge "MISSED SLA", `ApproachingSla` → orange "NEAR SLA",
  `InSla` → green "IN SLA"
- Missing ETA: show "⚠ No ETA" in orange
- Owner: show full name with alias in parentheses, e.g. "Richard Zhang (zhangrichard)"
- Due dates in the past: bold red
- PBI link: show as "AB#12345" hyperlink, or "None" if missing
- Truncate long status notes to ~100 chars with "..." in the table; full notes in tooltip/title

### Step 5: Save and Preview

1. Save HTML to `c:\Users\shjameel\Desktop\s360-report-{date}.html`
2. Open in browser for preview using `Start-Process` in terminal
3. Tell the user: "Report saved to Desktop. Preview opened in browser. Copy the HTML into a
   new Outlook email (Edit → Paste Special → HTML) and send to the team."

## SLA State Sort Order

1. `OutOfSla` (most urgent)
2. `ApproachingSla`
3. `InSla`

Within same SLA state, sort by `CurrentDueDate` ascending (earliest due first).

## Edge Cases

- **S360 MCP auth failure**: Instruct user to restart MCP server via Command Palette →
  `MCP: Restart Server` → `s360-breeze-mcp`
- **No items found**: Generate a celebratory "all clear" report
- **Multiple pages**: Paginate using `nextCursor` until all items are fetched
- **Owner alias empty**: Show "Unassigned" in the report and flag for attention
