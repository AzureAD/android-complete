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
- **ADO MCP Server** must be running (for PBI creation and lookup)
- **WorkIQ MCP Server** must be running (for pulling last week's email report)
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

### Step 3: Find Existing PBIs (Two Sources)

Before creating any new PBIs, search for existing ones from two sources.

#### 3a: Pull last week's S360 email via WorkIQ

Call `mcp_workiq_ask_work_iq` to find the most recent S360 report email:

```
question: "Find the most recent email with subject containing 'S360 Weekly Report' sent to androididentity@microsoft.com. Return the full email body content including any AB# work item references."
```

Parse the email body for:
- **AB# references** (e.g., `AB#12345`) — extract the number and the S360 item title nearby
- **Work item links** — ADO URLs like `dev.azure.com/.../workitems/12345`

Build a map of **S360 item title → AB# number** from the previous report.
These are known-good PBI assignments from last week.

If WorkIQ returns no results or the tool is unavailable, skip this step and continue
with Step 3b. Do not fail the workflow.

#### 3b: Search ADO for existing S360 PBIs

Search for work items that are tagged `s360` OR have `S360` in the title:

```
SELECT [System.Id], [System.Title], [System.State], [System.AssignedTo]
FROM WorkItems
WHERE ([System.Tags] CONTAINS 's360'
       OR [System.Title] CONTAINS 'S360')
AND [System.State] <> 'Done'
AND [System.State] <> 'Removed'
ORDER BY [System.CreatedDate] DESC
```

If the WIQL tool is unavailable, search via `mcp_ado_wit_my_work_items` and filter
results for titles containing `S360` or `[S360]`.

**Also**: if Step 3a returned AB# numbers, call `mcp_ado_wit_get_work_items_batch_by_ids`
to fetch their current state. This catches PBIs from last week's email that may have been
resolved since then — if state is `Done` or `Removed`, mark the S360 item as already
handled and exclude it from the "needs PBI" list.

Build a map of **S360 item title → AB# number + state** from ADO.

#### 3c: Merge PBI maps

For each S360 item, check if a PBI exists from any source:
1. The S360 API field `S360Dimensions.ADOWorkItemHTMLUrl` (already linked in S360)
2. Last week's email AB# references (from 3a) — confirmed human assignments
3. ADO search results (from 3b) — broader search, may include loosely related items

**Priority: S360-linked > last week's email > ADO search.**

Rationale: S360-linked is authoritative. Last week's email represents a confirmed OCE
assignment (higher confidence than a title search). ADO search is a fallback that may
produce false positives from unrelated items with "S360" in the title.

**Title matching logic** (for 3a and 3b):
- Normalize both titles: strip `[S360]` prefix, trim whitespace, lowercase
- Match if the S360 item title is **contained within** the ADO item title, or vice versa
- Example: S360 `"Update Vulnerable Container Image Reference"` matches
  ADO `"[S360] Update Vulnerable Container Image Reference"`

Mark each item's PBI status: `existing` (with AB# + URL), `needs-creation`, or
`resolved` (PBI exists but is Done/Removed — skip from report).

### Step 4: Create Missing PBIs

For items marked `needs-creation`:

1. **Present a summary** to the user showing which items need PBIs:
   ```
   The following S360 items have no ADO PBI:
   - [Title 1] — Owner: alias — SLA: OutOfSla
   - [Title 2] — Owner: alias — SLA: InSla
   Create PBIs for these items? (Y/N)
   ```

2. If user approves, discover ADO defaults using the same pattern as the `pbi-creator`
   skill's Step 2 (but call ADO MCP tools directly — do not invoke pbi-creator as a
   sub-skill since we have a simpler PBI structure with no dependency linking):
   - Call `mcp_ado_wit_my_work_items` to find recent work items
   - Extract area path, iteration path from the results
   - Use `mcp_ado_work_list_iterations` with `depth: 6` for current iterations
   - **Batch all questions** into a single `askQuestion` call:
     - Area Path (with discovered options)
     - Iteration (current/next month only)

3. For each item, create a PBI via `mcp_ado_wit_create_work_item`:
   ```json
   {
     "project": "Engineering",
     "workItemType": "Product Backlog Item",
     "fields": [
       {"name": "System.Title", "value": "[S360] <item Title>"},
       {"name": "System.Description", "value": "<HTML with S360 details>", "format": "Html"},
       {"name": "System.AreaPath", "value": "<confirmed area path>"},
       {"name": "System.IterationPath", "value": "<confirmed iteration>"},
       {"name": "System.AssignedTo", "value": "<OwnerAlias>@microsoft.com"},
       {"name": "Microsoft.VSTS.Common.Priority", "value": "<1=OutOfSla, 2=Approaching, 3=InSla>"},
       {"name": "System.Tags", "value": "s360; ai-generated"}
     ]
   }
   ```
   The description HTML should include:
   - S360 item title and service name
   - SLA state and due date
   - Link to S360 URL for remediation details
   - Current status notes (if any)
   - Wave information (if any)

4. Record the created AB# for each item.

### Step 5: Generate HTML Report

Read the Outlook HTML report prompt at `{{VSCODE_USER_PROMPTS_FOLDER}}/outlook-html-report.prompt.md`
to follow all rendering rules.

**Report structure:**

1. **Header** — "S360 Weekly Report — {date}" with subtitle "Android Auth Team"
2. **Summary banner** — Total items, Out of SLA count, Approaching SLA count, In SLA count,
   Missing ETA count. Use colored stat cards.
3. **🔴 Out of SLA section** — Table with columns: Title, Service, Owner, Due Date, ETA,
   Notes, PBI. Use red-tinted rows. Title is a **hyperlink to the S360 URL**.
4. **🟠 Approaching SLA section** — Same table format. Orange-tinted rows.
5. **🟢 In SLA section** — Same table format. Standard rows.

**Title column**: Each title should be a hyperlink to the S360 remediation URL (`item.s360_url`).
This lets readers click through to the S360 dashboard directly from the email.
6. **By Assignee breakdown** — Table: Assignee, Total, Out of SLA, Approaching, In SLA.
   Sorted by severity (most out-of-SLA first).
7. **Callout boxes** — Items Missing ETA, Unassigned items, Newly created PBIs.
8. **Footer** — "Auto-generated by S360 Reporter skill" + S360 dashboard link + date.

**PBI column display conventions:**
- Existing PBI: show as `AB#12345` hyperlinked to the ADO work item URL
  (URL format: `https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/<id>`)
- Newly created PBI: show as `AB#12345 🆕` with hyperlink
- No PBI (user declined creation): show "None" in italic
- Resolved PBI (from ADO search): omit from report (item was already handled)

**Other display conventions:**
- SLA State badges: `OutOfSla` → red badge "MISSED SLA", `ApproachingSla` → orange "NEAR SLA",
  `InSla` → green "IN SLA"
- Missing ETA: show "⚠ No ETA" in orange
- Owner: show full name with alias in parentheses, e.g. "Richard Zhang (zhangrichard)"
- Due dates in the past: bold
- Truncate long status notes to ~100 chars with "..." in the table

### Step 6: Save and Preview

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
- **WorkIQ unavailable or no email found**: Skip Step 3a entirely; rely on S360 API field
  and ADO search only. Do not fail the workflow.
- **ADO MCP unavailable**: Skip PBI creation; generate report with "None" in PBI column
  and a callout noting ADO was unavailable.
- **No items found**: Generate a celebratory "all clear" report
- **Multiple pages**: Paginate using `nextCursor` until all items are fetched
- **Owner alias empty**: Show "Unassigned" in the report and flag for attention
- **PBI already exists but title doesn't match exactly**: Use fuzzy matching — if an ADO
  item title contains the S360 item title (ignoring the `[S360]` prefix), consider it a match.
- **User declines PBI creation**: Proceed with report generation; show "None" in PBI column.
