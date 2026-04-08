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
- **Microsoft Graph MCP Server** must be running (for dynamic team member discovery via org chart)
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

### Step 0: Discover Team Members via Graph

Some S360 items (e.g., on-call readiness) are **person-targeted** (`TargetType: "Person"`,
`TargetId: "alias"`) rather than service-targeted. Searching by service tree IDs alone
misses these. To capture them, dynamically discover team member aliases from the org chart.

1. **Get current user's manager:**
   ```
   mcp_graph_microsoft_graph_get(
     relativeUrl: "/v1.0/me/manager?$select=id,displayName,userPrincipalName"
   )
   ```
   Extract the manager's `id`.

2. **Get all direct reports of the manager (= your teammates):**
   ```
   mcp_graph_microsoft_graph_get(
     relativeUrl: "/v1.0/users/{managerId}/directReports/graph.user?$count=true&$select=id,displayName,userPrincipalName,jobTitle,accountEnabled"
   )
   ```

3. **Filter and extract aliases:**
   - Exclude accounts where `userPrincipalName` starts with `SC-` or `sc-` (non-EA service accounts)
   - Exclude accounts where `accountEnabled` is `false`
   - Extract alias from `userPrincipalName` by stripping `@microsoft.com`
   - Store the list of aliases and a display-name map for later use in the report

**Fallback**: If the Graph MCP server is unavailable, fall back to the ADO Teams API:
- Call `mcp_ado_core_list_project_teams(project: "Engineering", mine: true)`
- Find the team named "Auth Client - Android" and note its `id`
- Call the ADO REST API via terminal:
  `Invoke-RestMethod -Uri "https://identitydivision.visualstudio.com/_apis/projects/Engineering/teams/{teamId}/members?api-version=7.1"`
- Extract `uniqueName` values, strip `@microsoft.com` to get aliases

### Step 1: Fetch S360 Data

Fetch items from **two sources** and merge them:

#### 1a: Service-targeted items

Call `mcp_s360-breeze-m_search_active_s360_kpi_action_items` with all three service tree IDs:

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

#### 1b: Person-targeted items

Using the aliases discovered in Step 0, call `mcp_s360-breeze-m_search_active_s360_kpi_action_items`
with `assignedTo`:

```
request: {
  "pageSize": 50,
  "assignedTo": ["alias1", "alias2", ...all team aliases from Step 0...]
}
```

This captures person-targeted items like on-call readiness checklists and certifications
that are tied to individuals rather than service tree IDs.

#### 1c: Merge results

Combine items from 1a and 1b. Deduplicate by `KpiActionItemId` — if the same item
appears in both searches, keep only one copy.

#### 1d: Detect Resolved Items (Week-over-Week)

To populate the "Resolved Since Last Week" section, compare the current S360 item set
against last week's report:

1. **Pull last week's S360 items** via one of these sources (in priority order):
   a. Call `mcp_s360-breeze-m_search_resolved_s360_kpi_action_items` with the same
      `targetIds` and `assignedTo` used in 1a/1b. This returns items that were active
      but have since been resolved.
   b. If the resolved search tool is unavailable, parse last week's email (from Step 3a)
      and extract the item titles + AB# numbers listed there.
   c. If neither is available, skip this step.

2. **Identify resolved items**: Items that appeared in last week's report but are NOT
   in the current active set (from 1c) are considered resolved.

3. **For each resolved item**, look up its ADO PBI state:
   - If the PBI is `Done` or `Removed`, mark as resolved with its AB# and assignee.
   - If the PBI is still open, the S360 item may have been resolved but the PBI wasn't
     closed — still include it in the resolved list but note the PBI state.

4. **Store the resolved items list** for use in Step 5 (report generation).
   Each resolved item should have: Title, AB#, Assignee, PBI State.

**Fallback**: If no previous report data is available, show a note in the report:
"No resolved items were detected this week. If items were resolved manually, they may
not appear here."

### Step 2: Parse and Deduplicate Items

The response contains an array at `result.resources`. For each item extract:

| Field | JSON Path | Notes |
|-------|-----------|-------|
| Title | `Title` | |
| Service | Map `TargetId` → service name from table above. For person-targeted items (`TargetType: \"Person\"`), use `CustomDimensions.TenantName` instead |
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
       {"name": "System.State", "value": "Committed"},
       {"name": "System.Tags", "value": "S360; AI-Generated"}
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

Read the **report template reference** at `.github/skills/s360-reporter/report-template.md`
for all HTML building blocks, color palette, and assembly order. Also read the Outlook HTML
report prompt at `{{VSCODE_USER_PROMPTS_FOLDER}}/outlook-html-report.prompt.md` (if available)
for general Outlook rendering rules (bgcolor, border-radius caveats, etc.).

**CRITICAL**: Copy HTML building blocks **verbatim** from `report-template.md`. Do NOT
rephrase, restyle, or improvise the HTML. Only substitute the `{{PLACEHOLDER}}` values
with actual data. The template was carefully designed and tested for Outlook compatibility
and visual consistency — any deviation (different padding, colors, font sizes, structure)
will break the design. If you need a component not in the template, replicate the closest
existing block's style exactly.

**Report structure:**

1. **Header** — Uppercase "S360 WEEKLY REPORT" label + large "Android Auth Team" title.
   Right side: blue pill badge with "Week of {date}". Below: services line listing all 3
   service names in bold.
2. **Summary cards** — 5 stat cards with colored top borders and tinted backgrounds:
   Total Items (blue), Out of SLA (red), Approaching (orange), In SLA (green),
   No ETA (amber). Cards have `border-radius:12px`.
3. **Severity bar** — Horizontal proportional bar showing red/orange/green distribution
   with counts. `border-radius:6px; overflow:hidden`.
4. **✅ Resolved Since Last Week** — Green left-bar callout box listing items resolved
   since the previous report. Each entry shows: **Title** — AB#link — assignee — **Done**.
   If no resolved items, show a note: "No resolved items were detected this week."
5. **Needs Attention** — Section header with red underline bar, followed by the Out of SLA
   card (red bordered, rounded, pink tint, detailed metadata layout with PBI chip).
6. **Items by Compliance Area** — Each program gets its own section with:
   - **Section header**: h3 title + italic subtitle + blue 3px underline bar
   - **Table**: columns Title, Service, Owner, SLA, Due, ETA, PBI
   - Blue-gray `#e8edf2` column headers, `border:1px solid` on all cells
   - Inline SLA badges per row (Missed/Near/In SLA pills)
   - Row background tints: `#fff5f5` for Missed, `#fff8f0` for Near, white/`#fafafa` zebra for In SLA
   - Left border color per title cell: red for Missed, orange for Near, green for In SLA
   - Programs ordered by worst SLA state first (programs with Missed items first, then Near, then In SLA)
   - Related items (e.g., CFS pipelines) grouped under a single program section
   - Typical program categories: GDPR & Data Classification, Continuous SDL, Vulnerability
     Management, MSRC Security Response, CFS Pipeline Onboarding, On-Call Readiness,
     PRC Violations. Derive program name directly from S360 API fields:
     `CustomDimensions.S360_WavesMetadata[0].ProgramDisplayName`, `CustomDimensions.filter`,
     `CustomDimensions.campaign`. For person-targeted items, use `CustomDimensions.TeamName`
     or a fallback label. See `report-template.md` for details.
7. **Ownership Breakdown** — Table: Assignee, Total, 🔴, 🟠, 🟢, No ETA.
   Blue-gray headers, zebra striped rows. Sorted by severity (most out-of-SLA first).
   No ETA column highlights values in amber.
8. **Action Required callouts** — Three left-bar callout boxes:
   - Red: Items needing owners (list item titles)
   - Amber: Items missing ETA (list item titles)
   - Blue: Newly created PBIs (list AB# links)
9. **Footer** — "Auto-generated by S360 Reporter" + S360 dashboard link + ADO Board link + date.

**Title column**: Each title should be a hyperlink to the S360 remediation URL (`item.s360_url`).
This lets readers click through to the S360 dashboard directly from the email.

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

- **Graph MCP unavailable**: Fall back to ADO Teams API (see Step 0 fallback). If both
  are unavailable, skip person-targeted search and proceed with service-targeted items only.
  Add a callout in the report noting that on-call items may be missing.
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
