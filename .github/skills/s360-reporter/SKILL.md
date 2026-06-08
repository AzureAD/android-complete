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
- **M365 User MCP** (`m365-user`) — for dynamic team member discovery via org chart
- **WorkIQ MCP Server** (optional) — used as fallback for pulling last week's email if the user doesn't provide it
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

## KPIs Where ETA Is Not Applicable

Some S360 KPIs do not have an ETA column in the portal. For items belonging to these KPIs,
show **"N/A"** in the ETA field instead of "Missing ETA ⚠". Do NOT count them in the
"Missing ETA" summary count.

| KPI ID | KPI Name |
|--------|----------|
| `d573888d-4c6f-81cc-7992-50dc17c87d83` | [Compl-CC1.3] Data Type Classification (GDPR) |

> **Maintaining this list**: If the user reports that an item shows "Missing ETA ⚠" but
> the S360 portal has no ETA column for it, add the KPI ID to this table.

## Workflow

### Step 0: Discover Team Members via Graph

Some S360 items (e.g., on-call readiness) are **person-targeted** (`TargetType: "Person"`,
`TargetId: "alias"`) rather than service-targeted. Searching by service tree IDs alone
misses these. To capture them, dynamically discover team member aliases from the org chart.

1. **Get current user's manager:**
   ```
   m365-user-GetManagerDetails(
     select: "id,displayName,userPrincipalName"
   )
   ```
   Extract the manager's `id` (GUID) and `userPrincipalName`.

2. **Get all direct reports of the manager (= your teammates):**
   ```
   m365-user-GetDirectReportsDetails(
     userId: "<manager UPN from step 1>",
     select: "id,displayName,userPrincipalName,jobTitle,accountEnabled"
   )
   ```

3. **Filter and extract aliases:**
   - Exclude accounts where `userPrincipalName` starts with `SC-` or `sc-` (non-EA service accounts)
   - Exclude accounts where `accountEnabled` is `false`
   - Extract alias from `userPrincipalName` by stripping `@microsoft.com`
   - Store the list of aliases and a display-name map for later use in the report

**Fallback**: If the M365 User MCP is unavailable, fall back to the ADO Teams API:
- Call `mcp_ado_core_list_project_teams(project: "Engineering", mine: true)`
- Find the team named "Auth Client - Android" and note its `id`
- Call the ADO REST API via terminal:
  `Invoke-RestMethod -Uri "https://identitydivision.visualstudio.com/_apis/projects/Engineering/teams/{teamId}/members?api-version=7.1"`
- Extract `uniqueName` values, strip `@microsoft.com` to get aliases

### Step 0b: Collect Last Week's Report

Before fetching S360 data, ask the user if they have last week's S360 report available.
This is the **primary method** for determining "new this week" items, resolved items,
and pre-existing PBI assignments. Use the `ask_user` tool:

```
question: "Do you have last week's S360 report to paste? This helps detect new/resolved items and avoid duplicate PBIs. You can paste the report text, or skip and I'll try to find it automatically."
choices: ["I'll paste it now", "Skip — find it automatically"]
```

**If the user pastes the report:**
1. Parse the pasted text for:
   - **Item titles with owners** — each row in the report table
   - **AB# references** — extract numeric ADO work item IDs (e.g., `AB#12345`, `Product Backlog Item 12345`, or `Bug 12345`)
   - **ADO work item URLs** — links like `dev.azure.com/.../workitems/12345`
   - **SLA states** — Missed SLA, Near SLA, In SLA
2. Build a **previous report map**: title → { pbi, owner, slaState }
3. Store this map for use in:
   - **Step 1e** (resolved items = items in last week's map but NOT in current active set)
   - **Step 3** (existing PBIs = AB# numbers from the map)
   - **Step 5** (new items = items in current active set but NOT in last week's map)

**If the user skips or doesn't respond:**
Fall back to automatic discovery in Step 3a (WorkIQ → Mail Search → proceed without).

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
- `TargetType` is `"Person"` AND `TargetId` exactly matches one of the team aliases
- `TargetId` matches one of our three service tree IDs
- `CustomDimensions.TenantName` contains "Auth Client", "MSAL", "ADAL", or "Authenticator"

**Critical — do NOT expand group items**: Each S360 item has exactly one `AssignedTo`
and one `TargetId`. Treat each item as-is — one row per `KpiActionItemId`. Never split
a single item into multiple rows by parsing names from the title or description. If
multiple team members share the same on-call KPI, S360 creates **separate items** for
each person (each with its own `KpiActionItemId` and `AssignedTo`). If an alias has no
matching item in the API response, that person simply has no action item — do not
fabricate one.

#### 1c: Merge results

Combine items from 1a and 1b. Deduplicate by `KpiActionItemId` — if the same item
appears in both searches, keep only one copy.

#### 1d: Fetch KPI Metadata (for Program Names)

After merging, collect the unique `KpiId` values from all items and fetch metadata
for each one:

```
mcp_s360-breeze-m_get_s360_kpi_metadata_by_kpi_id(kpiId: "<each unique KPI ID>")
```

Extract the `displayName` field from each response and build a **KpiId → displayName**
map. These display names are the authoritative program/category labels (e.g.,
`[SFI-ES4.2.4] Network Isolation for CFS endpoints`, `[Compl-CC1.3] Data Type
Classification`, `Individual On-Call Readiness`).

**Cache results** — multiple items share the same KpiId, so you only need one lookup
per unique KPI, not per item.

**Important**: Validate that the KpiId is a proper GUID (8-4-4-4-12 hex format) before
calling the API. Some raw data may have malformed IDs — if invalid, log a warning and
fall back to the item `Title` as the program name.

#### 1e: Detect Resolved Items (Week-over-Week)

To populate the "Resolved Since Last Week" section, compare the current S360 item set
against last week's report:

1. **Pull last week's S360 items** via one of these sources (in priority order):
   a. **User-provided report** (from Step 0b) — if the user pasted last week's report,
      use the parsed previous report map. This is the most reliable source.
   b. Call `mcp_s360-breeze-m_search_resolved_s360_kpi_action_items` with the same
      `targetIds` and `assignedTo` used in 1a/1b. Cross-reference results against the
      user-provided report if available — only include items that appear in BOTH sources.
   c. If no user-provided report and the resolved search tool is unavailable, parse
      last week's email (from Step 3a) and extract the item titles + AB# numbers.
   d. If none of the above are available, skip this step.

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
| Title | `Title` | **Required** — sanitize before display (see below). If empty, use KPI `displayName` from Step 1d. Never leave blank. |
| Service | Map `TargetId` → service name from table above. For person-targeted items (`TargetType: "Person"`), use `CustomDimensions.TenantName` instead |
| Owner Alias | `S360Dimensions.ActionOwnerAlias` | Falls back to `AssignedTo`. If both empty → "unassigned". **Overridden by ADO PBI assignee in Step 3e.** |
| Owner Name | `S360Dimensions.ActionOwner` | If empty, use the `nameMap` from Step 0 to look up alias → display name. If still empty, use the alias as display name. **Overridden by ADO PBI assignee in Step 3e.** |
| Due Date | `CurrentDueDate` | Format as `Mon DD, YYYY` |
| SLA State | `SLAState` | Values: `OutOfSla`, `ApproachingSla`, `InSla` |
| ETA | See **ETA Field Resolution** below — do NOT just read `CurrentETA` | If no ETA can be resolved from any of the candidate fields AND KpiId is in the "ETA Not Applicable" table → show **"N/A"**. If unresolved AND KpiId is NOT in that table → flag as **"Missing ETA ⚠"** |
| Status Notes | `CurrentStatus` | May be empty |
| Status Author | `CurrentStatusAuthor` | |
| ADO Work Item | `S360Dimensions.ADOWorkItemHTMLUrl` | Empty = no PBI linked |
| S360 URL | `URL` | Remediation/action link from S360 API (aka.ms, IcM, ADO, etc.) |
| KPI ID | `KpiId` | For dedup |
| Action Item ID | `KpiActionItemId` | For dedup |
| Program Name | KPI metadata `displayName` (from Step 1d) | For grouping items by compliance area |
| Program Desc | `CustomDimensions.S360_WavesMetadata[0].WaveDisplayName` | Subtitle under program heading (optional) |
| Wave | Extract from `CustomDimensions.S360_WavesMetadata[0].WaveDisplayName` |

**Program Name**: Use the **KPI metadata `displayName`** fetched in Step 1d via the
`KpiId → displayName` map. This is the only reliable source for program/category names.

Do **NOT** use any of these fields for program names — they contain internal codes:
- `CustomDimensions.initiative` — contains internal IDs like `"ADFunCompliance"`, `"CyberEO"`
- `CustomDimensions.filter` — contains codes like `"ADFunGlobal"`
- `CustomDimensions.campaign` — unreliable, often empty or internal
- `CustomDimensions.Ingestion_KpiName` — internal KPI ingestion label, not user-facing

**ETA Field Resolution** (CRITICAL — do not skip):

The S360 API does **not** always populate `CurrentETA` even when the S360 portal
shows an ETA. This is especially common for items where `SLAState == "OutOfSla"`
(Missed SLA) — the portal column reads "ETA (Missed SLA)" and the API surfaces
the value under a different field name. Reading only `CurrentETA` causes valid
ETAs to be reported as "No ETA" in the weekly report (a real bug reported by the
team).

To resolve an item's ETA, check these fields in order and use the first non-null
ISO-date value found:

1. `CurrentETA`
2. `ETA`
3. `MissedSLAETA` / `ETAMissedSLA` / `ETA_MissedSLA` (any casing/separator variant)
4. `S360Dimensions.ETA` / `S360Dimensions.CurrentETA` / `S360Dimensions.MissedSLAETA`
5. `CustomDimensions.ETA` / `CustomDimensions.CurrentETA` / `CustomDimensions.MissedSLAETA`
6. Any other top-level or nested key whose **name contains the substring `ETA`**
   (case-insensitive) and whose value parses as a valid ISO date or `YYYY-MM-DD`
   string. If multiple match, prefer the most recent (latest) date.

**Diagnostic fallback** — for any item where the resolved ETA is still null AND
the KpiId is NOT in the "ETA Not Applicable" table AND `SLAState == "OutOfSla"`,
dump the raw item JSON to the console and `grep -i eta` it. If a new field name
shows up, add it to the candidate list above (and to this file via a follow-up
PR) so future runs pick it up automatically.

**Do not** treat the S360 `SLAState`/`CurrentDueDate` fields as ETA — those are
separate (SLA deadline vs. owner's committed delivery date).

**Title sanitization**: S360 raw `Title` values often contain service tree GUIDs or
overly technical text that is not suitable for display. Apply these cleanups:

1. **Replace embedded GUIDs** — If the title contains a service tree ID (e.g.,
   `"8d0d308e-cd5c-44a3-9518-43eeeb424b57 has streams left to review"`), replace the
   GUID with the mapped service name (e.g., `"MSAL Android has streams left to review"`)
   or use a cleaner description from the KPI metadata.
2. **Set `shortTitle`** — For long titles (especially CFS pipeline items), extract the
   meaningful suffix. For example, `"Use CFS package feeds for pipeline: Publish msal
   to maven"` → shortTitle: `"Publish msal to maven"`.
3. **Clean up resolved item titles** — Apply the same sanitization to resolved items
   before rendering in the report.

**Dedup**: Some items appear multiple times with different `KpiActionItemId` but same
or similar `Title` and `TargetId`. Apply dedup in two passes:

1. **Exact dedup**: Group by `Title` + `TargetId`. If duplicates, keep the one with worst
   SLA state (`OutOfSla` > `ApproachingSla` > `InSla`).
2. **Fuzzy dedup**: After exact dedup, check for items with the same `KpiId` and
   overlapping title text. Items with the same KPI but targeting different services
   (e.g., CFS pipeline items targeting 8 endpoints) should be **merged into one row**
   with a note like "(8 endpoints)" rather than listed 8 times.

### Step 3: Find Existing Work Items (PBIs or Bugs)

Before creating any new PBIs, search for existing ADO work items that already
track each S360 item — these can be **either Product Backlog Items or Bugs**.
The team frequently files Bugs for S360 items that represent regressions or
defects (especially security/compliance items), and the skill must match those
just like it matches PBIs. **Do not restrict any lookup to `Product Backlog Item`
only.** Every search and merge step below applies equally to both work-item types.

#### 3a: Pull last week's S360 email

**Method 1: User-provided report** (from Step 0b) — If the user already pasted last
week's report, use the parsed map directly. This is the most reliable source and avoids
issues with Purview-encrypted emails or WorkIQ failures. **Skip Methods 2–3 entirely.**

**Method 2: WorkIQ** (fallback if user skipped Step 0b) — Call `mcp_workiq_ask_work_iq`:
```
question: "Find the most recent email from the last 7 days with subject containing 'S360 Weekly Report' sent to androididentity@microsoft.com. Return the full email body content including any AB# work item references."
```

**Method 3: Ask user** (fallback if WorkIQ errors) — Use `ask_user` to ask the user
to paste last week's report content.

Parse the email/report body for:
- **AB# references** (e.g., `AB#12345`) — extract the number and the S360 item title nearby.
  AB# is type-agnostic — the referenced work item may be a PBI **or a Bug** (or any other type).
- **Work item links** — ADO URLs like `dev.azure.com/.../workitems/12345` (also type-agnostic)
- **Item titles with owners** — build a title → (AB#, owner) map
- Both literal phrases **`Product Backlog Item 12345`** and **`Bug 12345`** map to the same
  AB# number; capture either

Build a map of **S360 item title → AB# number** from the previous report.
These are known-good PBI assignments from last week.

If all methods fail, skip this step and continue with Step 3b. Do not fail the workflow.

#### 3b: Search ADO for existing S360 work items (tag/title search)

Search for work items that are tagged `s360` OR have `S360` in the title.
The WIQL below intentionally queries `WorkItems` (not `WorkItemLinks` and not
type-filtered) so it returns **both Bugs and PBIs** — do not add a
`[System.WorkItemType]` filter here:

```
SELECT [System.Id], [System.Title], [System.State], [System.AssignedTo], [System.WorkItemType]
FROM WorkItems
WHERE ([System.Tags] CONTAINS 's360'
       OR [System.Title] CONTAINS 'S360')
AND [System.State] <> 'Done'
AND [System.State] <> 'Removed'
ORDER BY [System.CreatedDate] DESC
```

Capture `System.WorkItemType` for each result so downstream steps know whether
each matched item is a Bug or a PBI (useful in the report and for Step 4b).

If the WIQL tool is unavailable, search via `mcp_ado_wit_my_work_items` and filter
results for titles containing `S360` or `[S360]`. That tool also returns all
work-item types by default — do **not** restrict to PBIs.

**Also**: if Step 3a returned AB# numbers, call `mcp_ado_wit_get_work_items_batch_by_ids`
to fetch their current state. This catches work items from last week's email that may
have been resolved since then — if state is `Done` or `Removed`, mark the S360 item as
already handled and exclude it from the "needs PBI" list. Works for both Bugs and PBIs.

Build a map of **S360 item title → AB# number + state + work-item type** from ADO.

#### 3c: Keyword-based search for pre-existing work items (CRITICAL)

**Why this step exists**: Team members often create work items for S360 items
manually — without the `[S360]` prefix or tag, and frequently as **Bugs** rather
than PBIs (especially for security/compliance defects). Step 3b will MISS these
if the tag/title isn't present. Skipping this step creates **duplicate work
items**. This step is mandatory.

For each S360 item that was NOT matched in 3a or 3b, perform a **keyword search**
using the ADO work item search tool (`search_workitem`). **Explicitly include
both `Product Backlog Item` and `Bug` work-item types** — do not let the tool
default to PBIs only:

```
search_workitem(
  project: "Engineering",
  areaPath: "Engineering\\Auth Client\\Broker\\Android",
  searchText: "<2-4 distinctive keywords from the item title>",
  workItemType: ["Product Backlog Item", "Bug"],
  state: ["Committed", "New", "Active", "In Progress"],
  top: 5
)
```

If the `search_workitem` tool does not accept a `workItemType` parameter, run
the search without a type filter (the tool's default returns all types). Do
**NOT** post-filter the results down to PBIs only — keep Bug hits.

**Keyword extraction rules:**
- Use the most distinctive 2–4 words from the S360 item title
- Omit generic words like "required", "should", "the", "for", "is"
- Examples:
  - "MISE Compliance - 1.31.0+ [Wave 10]" → search: `"MISE Compliance Wave 10"`
  - "AuthN SDK - MSAL Android is required to onboard in Trusted Platform OneCompliance" → search: `"OneCompliance Trusted Platform onboard"`
  - "Establish and document a patch management process for DexGuard" → search: `"DexGuard patch management"`

**Matching logic:**
- If a result has the **same core meaning** as the S360 item (even without `[S360]`
  prefix), it's a match. Use title similarity — if 3+ significant words overlap,
  treat it as the same item.
- Record the pre-existing AB#, assignee, **and `System.WorkItemType`** (Bug or PBI).
- Bugs and PBIs are equally valid matches — the team uses Bugs for many security
  S360 items, so a Bug hit must be treated as "existing" just like a PBI hit.

**Batch for efficiency**: Group items into batches of 3–5 keyword searches at a time
to reduce round-trips. Items that are very unique (e.g., on-call checklists with
person names) can skip this step as they're unlikely to have pre-existing work items.

**Mark matched items as `existing`** — do NOT create new PBIs for them, regardless
of whether the existing item is a Bug or a PBI.

#### 3d: Merge work-item maps

For each S360 item, check if a work item (PBI or Bug) exists from any source:
1. The S360 API field `S360Dimensions.ADOWorkItemHTMLUrl` (already linked in S360 —
   the linked item may be a PBI or a Bug; the URL doesn't encode the type)
2. Last week's email AB# references (from 3a) — confirmed human assignments
3. ADO tag/title search results (from 3b) — items explicitly tagged S360 (Bug or PBI)
4. ADO keyword search results (from 3c) — catches manually-created items (Bug or PBI)
   without the S360 tag

**Priority: S360-linked > last week's email > keyword search (3c) > tag search (3b).**

Rationale: S360-linked is authoritative. Last week's email represents a confirmed OCE
assignment (higher confidence than a title search). Keyword search (3c) catches real
duplicates that lack the `[S360]` prefix. Tag search (3b) is broadest but may produce
false positives from unrelated items.

**Title matching logic** (for 3a, 3b, 3c):
- **Normalize both titles identically before comparing**: strip `[S360]` prefix, replace
  all non-alphanumeric characters (hyphens, underscores, brackets, etc.) with spaces,
  collapse multiple spaces to one, trim whitespace, lowercase. Apply the **same**
  normalizer to both the S360 title and the ADO/last-week-report title — mismatches
  occur when one side keeps punctuation (e.g., hyphens) and the other strips it.
- Match if the normalized S360 title is **contained within** the normalized ADO title,
  or vice versa
- For keyword search (3c): match if 3+ significant words overlap between titles
- Example: S360 `"MISE Compliance - 1.31.0+ [Wave 10]"` matches
  ADO `"[S360] MISE Compliance - 1.31.0+ [Wave 10]"` after normalization

Mark each item's status: `existing` (with AB# + URL + work-item type), `needs-creation`,
or `resolved` (work item exists but is Done/Removed — skip from report).

#### 3e: Override Owners from ADO Assignees

After matching is complete, for every item that has an existing work item (from any
source), fetch the `System.AssignedTo` field and **override the item's owner** with
the ADO assignee. This applies whether the matched work item is a PBI or a Bug —
both have a `System.AssignedTo` field. This ensures the report reflects the actual
owner, not the S360 default.

1. Collect all work-item IDs from matched items (Bugs and PBIs)
2. Call `mcp_ado_wit_get_work_items_batch_by_ids` with fields
   `["System.Id", "System.AssignedTo", "System.WorkItemType", "System.State"]`
3. For each item with a matched work item:
   - Extract alias from ADO `System.AssignedTo` (strip `@microsoft.com`)
   - Set `ownerAlias` = ADO alias
   - Set `ownerName` = ADO display name
4. Items WITHOUT a matched work item keep their S360-sourced owner (from Step 2)

**Rationale**: The S360 `ActionOwnerAlias` often defaults to the service dev owner or
a team lead, while the ADO work item has been explicitly assigned to the person doing
the work. The ADO assignment is more accurate for reporting purposes.

### Step 4: Create Missing PBIs

For items marked `needs-creation` (i.e., items where Step 3 found **neither** a
matching PBI **nor** a matching Bug):

> **Always create new items as Product Backlog Items**, never as Bugs. The skill
> only converts unmatched S360 items into PBIs — existing Bugs are matched and
> reused (Step 3), but we don't file new Bugs from this skill.

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
       {"name": "System.AssignedTo", "value": "<OwnerAlias>@microsoft.com"},  // OMIT this field if owner is empty — leave PBI unassigned
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

### Step 4b: Auto-Close Resolved Work Items

For each item in the **resolved items list** (from Step 1d) that has an associated ADO
work item (PBI **or Bug** — applies to both):

1. Look up the work item state via `mcp_ado_wit_get_work_items_batch_by_ids`
2. If the state is NOT `Done` or `Removed`, transition it to `Done` (works for both
   Bugs and PBIs — both types support the `Done` state in the Engineering project):
   ```
   mcp_ado_wit_update_work_item(
     id: <work_item_id>,
     updates: [{ path: "/fields/System.State", value: "Done" }]
   )
   ```
3. Log which work items were auto-closed for the report's "Resolved" section
   (include the type — e.g., `Bug AB#12345` vs `PBI AB#67890`)

If no work item is associated with a resolved item, skip it (no action needed).

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
      "shortTitle": "Abbreviated title (optional — generator falls back to title if null)",
      "service": "Service name (e.g., MSAL Android)",
      "ownerAlias": "alias",
      "ownerName": "Full Name",
      "sla": "OutOfSla | ApproachingSla | InSla",
      "due": "Mon DD, YYYY",
      "eta": "Mon DD, YYYY or null",
      "pbi": "12345 or null (ADO work item ID — PBI or Bug; generator adds AB# prefix)",
      "isNew": true,
      "s360Url": "https://s360.msftcloudes.com/...",
      "program": "Program display name",
      "programDesc": "Program subtitle/description (optional)",
      "subtitle": "Wave or campaign name (optional — renders inside TITLE column, do NOT set to service name since Service has its own column; leave null unless wave/campaign info is available)"
    }
  ],
  "resolved": [
    {
      "title": "Resolved item title",
      "assignee": "alias (just the alias — generator looks up display name from nameMap)",
      "pbi": "12345 or null (ADO work item ID — PBI or Bug; generator adds AB# prefix)"
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
- `items`: All active S360 items from Steps 1–4 with work-item info attached
- `resolved`: Items from Step 1d that are no longer active
- `nameMap`: Alias → display name mapping from Step 0 (used for ownership table)
- `newItems`: Items not found in last week's email (new this week) — for the info callout
- `isNew`: Set `true` for newly created PBIs (shows 🆕 badge)
- `pbi`: This field is the ADO work item ID and accepts **either a PBI or a Bug** ID
  (the generator's `pbiUrl()` builds a type-agnostic ADO URL that works for both).
  When the matched item is a Bug, still put its ID here — do not leave it null.

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

### Step 6: Save and Preview

1. **Save HTML** to `C:\Users\shjameel\Desktop\s360-report-{date}.html`

2. **Open in browser** for preview using `Start-Process` in terminal

3. Tell the user:
   ```
   Report saved to Desktop and preview opened in browser.
   To send: Open the browser preview → Select All (Ctrl+A) → Copy (Ctrl+C) →
   New Outlook email to androididentity@microsoft.com → Paste (Ctrl+V) → Send.
   ```

## SLA State Sort Order

1. `OutOfSla` (most urgent)
2. `ApproachingSla`
3. `InSla`

Within same SLA state, sort by `CurrentDueDate` ascending (earliest due first).

## Edge Cases

- **M365 User MCP unavailable**: Fall back to ADO Teams API (see Step 0 fallback). If both
  are unavailable, skip person-targeted search and proceed with service-targeted items only.
  Add a callout in the report noting that on-call items may be missing.
- **S360 MCP auth failure**:Instruct user to restart MCP server via Command Palette →
  `MCP: Restart Server` → `s360-breeze-mcp`
- **WorkIQ unavailable or no email found**: Skip Step 3a entirely; rely on S360 API field
  and ADO search only. Do not fail the workflow.
- **WorkIQ returns emails older than 7 days**: The query is scoped to "last 7 days" to
  ensure freshness. If no email is found within that window, proceed without previous report data.
- **ADO MCP unavailable**: Skip PBI creation; generate report with "None" in PBI column
  and a callout noting ADO was unavailable.
- **No items found**: Generate a celebratory "all clear" report
- **Multiple pages**: Paginate using `nextCursor` until all items are fetched
- **Owner alias empty**: Show "Unassigned" in the report and flag for attention.
  When creating PBIs, **omit the `System.AssignedTo` field entirely** — do NOT fall
  back to the manager or `AssignedTo` from S360. Leave the PBI unassigned so the team
  can triage it manually.
- **Work item (PBI or Bug) already exists but title doesn't match exactly**: Use fuzzy matching — if an ADO
  item title contains the S360 item title (ignoring the `[S360]` prefix), consider it a match.
- **Node.js unavailable**: Fall back to manually assembling HTML from `report-template.md`
  building blocks. Copy blocks verbatim and substitute placeholders.
- **Iteration computation edge cases**: The formula `CY{YY}Q{Q}_M{M}_{Mon}` uses calendar
  month number (M=1–12). At year boundaries (Dec→Jan), ensure year rolls over. At quarter
  boundaries, ensure Q increments correctly (e.g., March=Q1, April=Q2).
