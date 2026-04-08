---
name: s360-reporter
description: Generate S360 weekly reports for the Android Auth team. Fetches active action items from S360 MCP server, creates ADO work items (PBIs) for untracked items, and produces a polished Outlook-compatible HTML email report. Triggers include "S360 report", "generate S360 report", "weekly S360", "S360 status", "what are our S360 items", or any request to review, report on, or triage S360 action items for the Android Auth team.
---

# S360 Weekly Report Generator

Generate a polished S360 weekly report for the Android Auth team. Fetches live data from
the S360 MCP server, ensures every item has an ADO PBI, and produces an Outlook-compatible
HTML email report.

## Prerequisites

- **Node.js** must be available (for the committed report generator script)
- **S360 MCP Server** must be running (configured in `.vscode/mcp.json` as `s360-breeze-mcp`)
- **ADO MCP Server** must be running (for PBI creation and lookup)
- **Microsoft Graph MCP Server** must be running (for dynamic team member discovery via org chart,
  and optionally for creating an Outlook draft email)
- **WorkIQ MCP Server** must be running (for pulling last week's email report)
- Read the **Outlook HTML report prompt** at `{{VSCODE_USER_PROMPTS_FOLDER}}/outlook-html-report.prompt.md`
  for HTML rendering rules before generating the report

## Quick Mode

If the user says "quick S360" or "S360 status", run **Steps 0–2 only** and print a CLI
summary instead of generating the full report. Example output:

```
S360 Status — Android Auth Team (Apr 8, 2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
15 active items: 1 🔴 Out of SLA, 2 🟠 Approaching, 12 🟢 In SLA
7 items missing ETA

🔴 GDPR Streams Left to Review — moghosh — Due Mar 22 (17 days overdue)
🟠 Disable local auth for container registries — moghosh — Due Apr 12
🟠 Threat Model Review — zhangrichard — Due May 5
```

Skip PBI creation, report generation, and email drafting in quick mode.

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

**Important**: The `assignedTo` search returns ALL items for those aliases across Microsoft,
including items from other team memberships. After fetching, filter results to only include
items where one of these conditions is met:
- `TargetType` is `"Person"` (on-call, certifications — always relevant to the person)
- `TargetId` matches one of our three service tree IDs
- `CustomDimensions.TenantName` contains "Auth Client", "MSAL", "ADAL", or "Authenticator"

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

Call `mcp_workiq_ask_work_iq` to find the most recent S360 report email **from the last 7 days**:

```
question: "Find the most recent email from the last 7 days with subject containing 'S360 Weekly Report' sent to androididentity@microsoft.com. Return the full email body content including any AB# work item references."
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

1. **Show a summary** of what will be created (no confirmation needed — always create):
   ```
   Creating PBIs for 14 S360 items:
   - [Title 1] — Owner: alias — SLA: OutOfSla — Priority 1
   - [Title 2] — Owner: alias — SLA: InSla — Priority 3
   ...
   ```

2. **Use default ADO configuration:**
   - **Area Path**: `Engineering\Auth Client\Broker\Android`
   - **Iteration**: Compute from the current date (see formula below)
   - **State**: `Committed`
   - **Tags**: `S360; AI-Generated`
   - **Priority**: `1` for OutOfSla, `2` for ApproachingSla, `3` for InSla

   **Iteration computation** (from current date):
   ```
   month = current month (1-12)
   year2 = last 2 digits of year (e.g., 26)
   quarter = ceil(month / 3)  (1-4)
   half = quarter <= 2 ? 1 : 2
   monthAbbr = Jan|Feb|...|Dec

   path = Engineering\CY{year2}\CY{year2}H{half}\CY{year2}Q{quarter}\Monthly\CY{year2}Q{quarter}_M{month}_{monthAbbr}
   ```
   Example for April 2026: `Engineering\CY26\CY26H1\CY26Q2\Monthly\CY26Q2_M4_Apr`

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

### Step 4b: Auto-Close Resolved PBIs

For each item in the **resolved items list** (from Step 1d) that has an associated ADO PBI:

1. Look up the PBI state via `mcp_ado_wit_get_work_items_batch_by_ids`
2. If the PBI state is NOT `Done` or `Removed`, transition it to `Done`:
   ```
   mcp_ado_wit_update_work_item(
     id: <pbi_id>,
     updates: [{ path: "/fields/System.State", value: "Done" }]
   )
   ```
3. Log which PBIs were auto-closed for the report's "Resolved" section

If no PBI is associated with a resolved item, skip it (no action needed).

### Step 5: Generate HTML Report

Use the **committed generator script** at `.github/skills/s360-reporter/generate-report.js`
to produce the HTML report. This script is data-driven — you prepare a JSON input file and
the script handles all HTML rendering, Outlook compatibility, and styling.

#### 5a: Prepare JSON input file

Write a JSON file to a temp location (e.g., `$env:TEMP/s360_data.json`) with this schema:

```json
{
  "reportDate": "YYYY-MM-DD",
  "items": [
    {
      "title": "S360 item title",
      "shortTitle": "Abbreviated title for cards (optional, falls back to title)",
      "service": "Service name (e.g., MSAL Android)",
      "ownerAlias": "alias",
      "ownerName": "Full Name",
      "sla": "OutOfSla | ApproachingSla | InSla",
      "due": "Mon DD, YYYY",
      "eta": "Mon DD, YYYY or null",
      "pbi": "AB#12345 or null",
      "isNew": true,
      "s360Url": "https://s360.msftcloudes.com/...",
      "program": "Program display name",
      "programDesc": "Program subtitle/description (optional)",
      "subtitle": "Wave or campaign name (optional)"
    }
  ],
  "resolved": [
    {
      "title": "Resolved item title",
      "assignee": "Full Name (alias)",
      "pbi": "AB#12345 or null"
    }
  ],
  "nameMap": {
    "alias": "Full Name"
  },
  "newItems": [
    { "title": "New item title", "service": "Service name" }
  ]
}
```

**Field notes:**
- `items`: All active S360 items from Steps 1–4 with PBI info attached
- `resolved`: Items from Step 1d that are no longer active
- `nameMap`: Alias → display name mapping from Step 0 (used for ownership table)
- `newItems`: Items not found in last week's email (new this week) — for the info callout
- `isNew`: Set `true` for newly created PBIs (shows 🆕 badge)

#### 5b: Run the generator

```powershell
node .github/skills/s360-reporter/generate-report.js --input "$env:TEMP/s360_data.json" --output "C:\Users\shjameel\Desktop\s360-report-{date}.html"
```

The script produces a fully styled Outlook-compatible HTML report with:
- Header with team name, date badge, and service list
- Summary cards (Total, Out of SLA, Approaching, In SLA, No ETA)
- Severity distribution bar
- "New This Week" info callout (if any new items)
- Resolved items section
- Needs Attention cards for Out of SLA items
- Items by Compliance Area tables (grouped by program, sorted by severity)
- Ownership breakdown table
- Action Required callouts (missing owners, missing ETAs, new PBIs)
- Footer with dashboard links

**Visual reference**: See `report-template.md` for the HTML building blocks, color palette,
and design rationale. The generator script implements these blocks programmatically.

**Fallback**: If Node.js is unavailable, fall back to manually assembling HTML using the
building blocks in `report-template.md` — copy them verbatim and substitute placeholders.

### Step 6: Save, Draft Email, and Preview

1. **Save HTML** to `C:\Users\shjameel\Desktop\s360-report-{date}.html`

2. **Draft Outlook email via Graph API** (if available):
   ```
   mcp_graph_microsoft_graph_suggest_queries(
     intentDescription: "create a draft email message"
   )
   ```
   Then call `mcp_graph_microsoft_graph_post` with the suggested endpoint:
   ```
   relativeUrl: "/v1.0/me/messages"
   body: {
     "subject": "S360 Weekly Report — Android Auth Team — Week of {date}",
     "body": { "contentType": "HTML", "content": "<full HTML report>" },
     "toRecipients": [
       { "emailAddress": { "address": "androididentity@microsoft.com" } }
     ],
     "isDraft": true
   }
   ```
   If the Graph call succeeds, tell the user: "📧 Outlook draft created. Open Outlook →
   Drafts → review and send."

   **Fallback**: If Graph MCP is unavailable or the call fails, fall back to the file-based
   approach (step 3 below).

3. **Open in browser** for preview using `Start-Process` in terminal

4. Tell the user the report location and next steps:
   - If email draft was created: "Report saved to Desktop and Outlook draft created."
   - If file-only: "Report saved to Desktop. Preview opened in browser. Copy the HTML
     into a new Outlook email (Edit → Paste Special → HTML) and send to the team."

## SLA State Sort Order

1. `OutOfSla` (most urgent)
2. `ApproachingSla`
3. `InSla`

Within same SLA state, sort by `CurrentDueDate` ascending (earliest due first).

## Edge Cases

- **Graph MCP unavailable**: Fall back to ADO Teams API (see Step 0 fallback). If both
  are unavailable, skip person-targeted search and proceed with service-targeted items only.
  Add a callout in the report noting that on-call items may be missing.
- **Graph email draft fails**: Fall back to file-based approach — save HTML to Desktop and
  instruct user to copy/paste into Outlook manually. Do not fail the workflow.
- **S360 MCP auth failure**: Instruct user to restart MCP server via Command Palette →
  `MCP: Restart Server` → `s360-breeze-mcp`
- **WorkIQ unavailable or no email found**: Skip Step 3a entirely; rely on S360 API field
  and ADO search only. Do not fail the workflow.
- **WorkIQ returns emails older than 7 days**: The query is scoped to "last 7 days" to
  ensure freshness. If no email is found within that window, proceed without previous report data.
- **ADO MCP unavailable**: Skip PBI creation; generate report with "None" in PBI column
  and a callout noting ADO was unavailable.
- **No items found**: Generate a celebratory "all clear" report
- **Multiple pages**: Paginate using `nextCursor` until all items are fetched
- **Owner alias empty**: Show "Unassigned" in the report and flag for attention
- **PBI already exists but title doesn't match exactly**: Use fuzzy matching — if an ADO
  item title contains the S360 item title (ignoring the `[S360]` prefix), consider it a match.
- **Node.js unavailable**: Fall back to manually assembling HTML from `report-template.md`
  building blocks. Copy blocks verbatim and substitute placeholders.
- **Iteration computation edge cases**: The formula `CY{YY}Q{Q}_M{M}_{Mon}` uses calendar
  month number (M=1–12). At year boundaries (Dec→Jan), ensure year rolls over. At quarter
  boundaries, ensure Q increments correctly (e.g., March=Q1, April=Q2).
