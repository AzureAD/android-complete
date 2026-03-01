---
name: pbi-creator
description: Create Azure DevOps work items from a feature plan produced by the `feature-planner` skill. Handles ADO metadata discovery (area path, iteration, assignee), work item creation, and dependency linking. Use this skill when PBIs have been planned and approved, and you need to create them in ADO. Triggers include "create the PBIs", "create work items", "push PBIs to ADO", or approval of a feature plan.
---

# PBI Creator

Create Azure DevOps work items from a feature plan produced by the `feature-planner` skill.
Handles ADO metadata discovery, work item creation, and dependency linking.

## Prerequisites

- **ADO MCP Server** must be running (configured in `.vscode/mcp.json`) with `work-items` domain
- A **feature plan** must exist in the current chat context (produced by the `feature-planner` skill)
  — the plan follows the structured output format with Summary Table, PBI Details, etc.

## Workflow

### Step 1: Parse the Feature Plan

Read the feature plan from the chat context. Extract for each PBI:
- **Title** — from the `#### PBI-N: [Title]` header
- **Repo** — from the metadata table `Repo` field
- **Module** — from the metadata table `Module` field
- **Priority** — from the metadata table `Priority` field (P1→1, P2→2, P3→3)
- **Depends on** — from the metadata table `Depends on` field (PBI-N references)
- **Tags** — from the metadata table `Tags` field
- **Description** — from the `##### Description` section (in plain markdown).
  **Convert to HTML** before setting as `System.Description` in ADO:
  - `## Heading` → `<h2>Heading</h2>`
  - `**bold**` → `<strong>bold</strong>`
  - `- item` → `<ul><li>item</li></ul>`
  - `` `code` `` → `<code>code</code>`
  - Paragraphs → `<p>text</p>`
  - Or use a simple approach: wrap the entire markdown in `<pre>` tags if conversion is complex.

If a feature plan is not found in context, ask the developer:
> "I don't see a feature plan in our conversation. Either:
> 1. Run the `feature-planner` skill first (say 'plan this feature')
> 2. Or paste the PBI details and I'll create the work items"

### Step 2: Discover ADO Defaults

**Do this BEFORE asking the developer for preferences.** This ensures you present valid
options and avoid path errors.

**Discovery sequence:**
1. Call `mcp_ado_wit_my_work_items` to get the developer's recent work items.
2. Call `mcp_ado_wit_get_work_items_batch_by_ids` on 3-5 recent items.
3. Extract from the responses:
   - `System.AreaPath` → collect **all unique area paths** with frequency counts
   - `System.IterationPath` → note the format pattern
   - `System.AssignedTo` → use as default assignee
4. Call `mcp_ado_work_list_iterations` with **`depth: 6`** (monthly sprints are at depth 6;
   `depth: 4` will miss them).
5. Filter iterations to **current month or future only** — discard any iteration whose end date
   is in the past. Compare iteration dates against today's date. For monthly iterations with
   names like `CY26Q1_M2_Feb`, parse the month/year and exclude months before the current one.
   **Never suggest past iterations as options.**

### Step 3: Present Options for Confirmation

**MANDATORY**: You MUST present options and wait for the developer to confirm before proceeding.
Do NOT make assumptions. Do NOT auto-select defaults. Always ask.
**Use the `askQuestion` tool** to present clickable MCQ-style options whenever possible.

**Batch ALL questions into a SINGLE `askQuestion` call** with multiple questions.
This gives the user a smooth flow where answering one immediately shows the next.
Do NOT make separate `askQuestion` calls for each question — combine them all into one call
with 4 questions (Area Path, Iteration, Assignee, Parent). Example:

```
askQuestion({
  questions: [
    {
      header: "Area Path",
      question: "Which area path for these PBIs?",
      options: [
        { label: "Engineering\\Auth Client\\Broker\\Android", description: "Your most recent work items use this path", recommended: true },
        { label: "Engineering\\Auth Client\\MSAL\\Android" }
      ],
      allowFreeformInput: true
    },
    {
      header: "Iteration",
      question: "Which iteration? (Current date: <today>)",
      options: [
        { label: "CY26Q2_M4_Apr (next month)", description: "Engineering\\CY26\\CY26H1\\CY26Q2\\Monthly\\CY26Q2_M4_Apr", recommended: true },
        { label: "CY26Q2_M5_May", description: "Engineering\\CY26\\CY26H1\\CY26Q2\\Monthly\\CY26Q2_M5_May" }
      ],
      allowFreeformInput: true
    },
    {
      header: "Assignee",
      question: "Who should be assigned?",
      options: [
        { label: "shjameel@microsoft.com", description: "Discovered from recent work items", recommended: true }
      ],
      allowFreeformInput: true
    },
    {
      header: "Parent",
      question: "Link PBIs to a parent Feature work item?",
      options: [
        { label: "Create new Feature", description: "Create a new Feature work item titled '<feature name>'" },
        { label: "No parent", description: "No parent Feature — standalone PBIs" }
      ],
      allowFreeformInput: true
    }
  ]
})
```

### Question details:

**Area Path** (ALWAYS ask, even if only one found):
- Show all unique area paths discovered from recent work items with frequency counts
- Mark the most common one as `recommended: true`

**Iteration** (ALWAYS present a list — never assume):
Only show iterations for the **current month or later**. Never include past months.
- Mark the current/next month as `recommended: true`

**Assignee** (confirm):
- Show the discovered assignee as `recommended: true`

**Parent Feature**:
- Offer to create a new Feature, link to an existing one, or no parent

Wait for all answers before proceeding to Step 4.

**If creating a new Feature:**
Use `mcp_ado_wit_create_work_item` with:
```json
{
  "project": "Engineering",
  "workItemType": "Feature",
  "fields": [
    {"name": "System.Title", "value": "[Feature Name from plan header]"},
    {"name": "System.Description", "value": "<p>[Brief feature description from plan]</p>", "format": "Html"},
    {"name": "System.AreaPath", "value": "[developer-confirmed area path]"},
    {"name": "System.IterationPath", "value": "[developer-confirmed iteration]"},
    {"name": "System.AssignedTo", "value": "[developer-confirmed assignee]"},
    {"name": "System.Tags", "value": "ai-generated"}
  ]
}
```
Record the Feature ID for use in Step 4.

**If linking to existing Feature:**
Verify the Feature exists by calling `mcp_ado_wit_get_work_item` with the provided ID.
Record the Feature ID for use in Step 4.

### Step 4: Create Work Items in ADO

Use `mcp_ado_wit_create_work_item` for each PBI. Create them in **dependency order**
(PBIs with no dependencies first).

**CRITICAL tool parameters:**
- `project`: `"Engineering"`
- `workItemType`: `"Product Backlog Item"`
- `fields`: An array of `{name, value}` objects

**Field format:**
```json
{
  "project": "Engineering",
  "workItemType": "Product Backlog Item",
  "fields": [
    {"name": "System.Title", "value": "[title from plan]"},
    {"name": "System.Description", "value": "[HTML from <details> block]", "format": "Html"},
    {"name": "System.AreaPath", "value": "[developer-confirmed area path]"},
    {"name": "System.IterationPath", "value": "[developer-confirmed iteration]"},
    {"name": "System.AssignedTo", "value": "[developer-confirmed assignee]"},
    {"name": "Microsoft.VSTS.Common.Priority", "value": "[priority number]"},
    {"name": "System.Tags", "value": "[tags from plan]"}
  ]
}
```

**Common mistakes to avoid:**
- Do NOT use top-level params like `title`, `description`, `areaPath` — they don't exist
- Do NOT use `type` — the param is called `workItemType`
- The `description` field value must be **HTML** (not Markdown), with `"format": "Html"`
- Tags are semicolon-separated: `"ai-generated; copilot-agent-ready"`
- Area/iteration paths use backslashes: `"Engineering\\Auth Client\\Broker\\Android"`
- **Never hardcode paths** — always use values confirmed by the developer in Step 3

**After each PBI is created:**
- Record the returned `id` (AB# number)
- Map `PBI-N` → `AB#[id]` for dependency resolution

### Step 5: Resolve Dependencies + Parent Links

After all PBIs are created and you have the PBI-N → AB# mapping:

1. **Update descriptions**: For each PBI whose description references `PBI-N` in the
   Dependencies section, update the description to use the actual `AB#[id]`.
   Use `mcp_ado_wit_update_work_item` to patch the description.

2. **Link dependencies**: Use `mcp_ado_wit_work_items_link` to create predecessor links:
   ```json
   {
     "updates": [
       {"id": [dependent_id], "linkToId": [dependency_id], "type": "predecessor",
        "comment": "[Dependent title] depends on [Dependency title]"}
     ]
   }
   ```

3. **Parent to Feature** (if Feature ID was recorded in Step 3.5):
   Use `mcp_ado_wit_add_child_work_items` to parent all PBIs to the Feature:
   ```json
   {
     "parentId": [feature_id],
     "childWorkItemIds": [pbi_id_1, pbi_id_2, pbi_id_3]
   }
   ```
   If this tool is unavailable, use `mcp_ado_wit_work_items_link` with `type: "child"` instead.

### Step 5.5: Mark PBIs as Committed

After all PBIs are created, linked, and parented, update their state to **Committed**
so they appear in sprint planning.

Use `mcp_ado_wit_update_work_items_batch` (if available) or `mcp_ado_wit_update_work_item`
for each PBI:
```json
{
  "id": [pbi_id],
  "fields": [
    {"name": "System.State", "value": "Committed"}
  ]
}
```

Also update the Feature work item (if created) to Committed state.

### Step 6: Report Summary

Present the results:

```markdown
## PBIs Created: [Feature Name]

### Work Items

| PBI | AB# | Title | Repo | Depends On | State | Link |
|-----|-----|-------|------|------------|-------|------|
| PBI-1 | AB#12345 | [title] | common | — | Committed | [link] |
| PBI-2 | AB#12346 | [title] | broker | AB#12345 | Committed | [link] |
| PBI-3 | AB#12347 | [title] | msal | AB#12345 | Committed | [link] |

### ADO Settings Used

- **Parent Feature**: AB#12340 `[Feature title]` (or "None")
- **Area Path**: `[confirmed path]`
- **Iteration**: `[confirmed path]`
- **Assigned to**: `[confirmed assignee]`
- **State**: Committed

### Dispatch Order

1. Dispatch **AB#12345** first (no blockers)
2. After AB#12345 merges → dispatch **AB#12346** and **AB#12347** in parallel

### Next Step

> Say **"dispatch"** to send PBI-1 to Copilot coding agent via the `pbi-dispatcher` skill.
```

## MCP Server Failure Recovery

If ADO MCP tools become unavailable mid-workflow:
1. Ask the developer to restart the MCP server:
   Command Palette → `MCP: Restart Server` → `ado`
2. If tools still don't load, recommend starting a **new chat session** — MCP tools sometimes
   don't reconnect to existing sessions after a restart.
3. **Preserve progress**: Note which PBIs were already created (with AB# IDs) so the new
   session can continue from where it left off without duplicating work items.
4. In the new session, the developer can say:
   > "Continue creating PBIs for [feature]. PBI-1 already created as AB#12345. Create PBI-2 onwards."

## Edge Cases

### Plan has a single PBI
Skip dependency linking. Create one work item and report.

### Developer wants different area paths per PBI
If PBIs target different teams (e.g., one in Common, one in MSAL), ask if they want different
area paths. Present the discovered options for each PBI individually.

### Developer modifies the plan before approving
If the developer asks for changes to the plan (add/remove PBIs, change descriptions), defer back
to the `feature-planner` skill to regenerate the plan, then return here for creation.
