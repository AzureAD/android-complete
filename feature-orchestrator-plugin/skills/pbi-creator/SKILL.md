---
name: pbi-creator
description: Create work items in Azure DevOps from a feature plan. Handles ADO metadata discovery (area path, iteration, assignee), work item creation, and dependency linking. Triggers include "create the PBIs", "create work items", "push PBIs to ADO".
---

# PBI Creator

Create Azure DevOps work items from a feature plan produced by the `feature-planner` skill.

## Configuration

Read `.github/orchestrator-config.json` for:
- `ado.project` — ADO project name (e.g., "Engineering")
- `ado.org` — ADO organization name (e.g., "IdentityDivision")
- `ado.workItemType` — work item type (default: "Product Backlog Item")
- `ado.iterationDepth` — depth for iteration discovery (default: 6)

### ⚠️ ADO Org/Project Parsing

The `ado.org` and `ado.project` fields should contain **plain names only**, not full URLs.
If the config contains a URL, extract the relevant part:
- `https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/123` → org: `IdentityDivision`, project: `Engineering`
- `https://msazure.visualstudio.com/One/_git/repo` → org: `msazure`, project: `One`
- `IdentityDivision` → use as-is

When calling MCP tools, pass only the **org name** (e.g., `IdentityDivision`) and
**project name** (e.g., `Engineering`), never a full URL with `https:`. URLs with colons
cause ADO API errors: "A potentially dangerous Request.Path value was detected."

## Prerequisites

- **ADO MCP Server** must be running (configured in `.mcp.json`)
- A **feature plan** in the current chat context (from `feature-planner` skill)

## Workflow

### Step 1: Parse the Feature Plan

Read the plan from chat context. Extract for each work item:
- **Title** — from `#### WI-N: [Title]` header
- **Repo** — from metadata table
- **Module** — from metadata table
- **Priority** — P1→1, P2→2, P3→3
- **Depends on** — WI-N references
- **Tags** — from metadata table
- **Description** — from `##### Description` section. **Convert to HTML** for ADO:
  - `## Heading` → `<h2>Heading</h2>`
  - `**bold**` → `<strong>bold</strong>`
  - `- item` → `<ul><li>item</li></ul>`
  - Or wrap in `<pre>` tags if conversion is complex

If no plan found, ask: "Run the `feature-planner` skill first, or paste PBI details."

### Step 2: Discover ADO Defaults

**Do this BEFORE asking the developer.** This ensures valid options.

1. Call `mcp_ado_wit_my_work_items` to get recent work items
2. Call `mcp_ado_wit_get_work_items_batch_by_ids` on 3-5 recent items
3. Extract:
   - `System.AreaPath` — all unique paths with frequency counts
   - `System.IterationPath` — note the pattern
   - `System.AssignedTo` — default assignee
4. Call `mcp_ado_work_list_iterations` with `depth` from config (default 6)
5. **Filter iterations to current month or future only** — discard past iterations

### Step 3: Present Options for Confirmation

## ⛔ HARD STOP — DO NOT SKIP THIS STEP

**You MUST complete Step 2 and Step 3 BEFORE creating any work items.**
Do NOT proceed to Step 4 until the developer has answered ALL four questions.
This is not optional. This is not a suggestion. **STOP HERE and ask.**

If you skip this step and auto-select defaults, the work items will be created
in the wrong area path, wrong iteration, or wrong assignee — and the developer
will have to manually fix every single one.

**Batch ALL questions into a SINGLE `askQuestion` call:**

```
askQuestion({
  questions: [
    {
      header: "Area Path",
      question: "Which area path?",
      options: [
        { label: "<most common path>", description: "From your recent work items", recommended: true },
        { label: "<other path>" }
      ],
      allowFreeformInput: true
    },
    {
      header: "Iteration",
      question: "Which iteration? (Current date: <today>)",
      options: [
        { label: "<next month>", description: "<full iteration path>", recommended: true },
        { label: "<month after>" }
      ],
      allowFreeformInput: true
    },
    {
      header: "Assignee",
      question: "Who should be assigned?",
      options: [
        { label: "<discovered email>", description: "From recent work items", recommended: true }
      ],
      allowFreeformInput: true
    },
    {
      header: "Parent",
      question: "Link to a parent Feature work item?",
      options: [
        { label: "Create new Feature", description: "New Feature titled '<feature name>'" },
        { label: "No parent", description: "Standalone PBIs" }
      ],
      allowFreeformInput: true
    }
  ]
})
```

Wait for ALL answers before proceeding.

### Step 4: Create Work Items

Use `mcp_ado_wit_create_work_item` for each item in **dependency order**.

**CRITICAL parameters** (read project from config):
```json
{
  "project": "<from config: ado.project>",
  "workItemType": "<from config: ado.workItemType>",
  "fields": [
    {"name": "System.Title", "value": "[title]"},
    {"name": "System.Description", "value": "[HTML description]", "format": "Html"},
    {"name": "System.AreaPath", "value": "[confirmed path]"},
    {"name": "System.IterationPath", "value": "[confirmed iteration]"},
    {"name": "System.AssignedTo", "value": "[confirmed assignee]"},
    {"name": "Microsoft.VSTS.Common.Priority", "value": "[number]"},
    {"name": "System.Tags", "value": "[semicolon-separated tags]"}
  ]
}
```

**Common mistakes to avoid:**
- Do NOT use top-level `title`, `description`, `areaPath` — they don't exist
- The param is `workItemType`, NOT `type`
- Description must be **HTML** with `"format": "Html"`
- Tags are semicolon-separated
- Area/iteration paths use backslashes
- **Never hardcode paths** — use developer-confirmed values
- **MUST include Area Path AND Iteration Path** — these come from Step 3 confirmations.
  If you don't have them, you skipped Step 3. Go back.

### ⚠️ Title Sanitization

**Remove colons (`:`) from work item titles.** The ADO REST API encodes titles in the
URL path, and colons trigger an HTTP 400 error: "A potentially dangerous Request.Path
value was detected from the client (:)."

Instead of: `WI-1: Add feature flag and ECS flight`
Use: `WI-1 — Add feature flag and ECS flight` (em-dash) or just `Add feature flag and ECS flight`

Also avoid these characters in titles: `<`, `>`, `#`, `%`, `{`, `}`, `|`, `\`, `^`, `~`, `[`, `]`, `` ` ``

### ⚠️ NEVER Create Work Items With Minimal Descriptions

**Every work item MUST include the FULL description from the feature plan.** This is the
entire point of the orchestrator — the coding agent implements from the PBI description alone.

If `mcp_ado_wit_create_work_item` fails:
1. **Check the error** — is it a title character issue? Sanitize and retry.
2. **Retry the same tool** with corrected input.
3. **If the tool keeps failing**, report the error to the developer and ask them to help
   troubleshoot the MCP server.

**NEVER fall back to a different tool that creates work items without the full description.**
**NEVER tell the user "descriptions are summaries" or suggest they update them manually.**
If you can't create work items with full descriptions, STOP and report the failure.
A PBI without a proper description is worse than no PBI at all.

After each creation, record the returned `id` and map WI-N → AB#[id].

### Step 5: Resolve Dependencies + Parent Links

1. **Update descriptions**: Replace WI-N references with AB#[id] in each description
2. **Link dependencies**: Use `mcp_ado_wit_work_items_link`:
   ```json
   {"updates": [{"id": [dependent], "linkToId": [dependency], "type": "predecessor"}]}
   ```
3. **Parent to Feature** (if created): Use `mcp_ado_wit_add_child_work_items`

### Step 5.5: Mark as Committed

Update all work items to **Committed** state:
```json
{"id": [id], "fields": [{"name": "System.State", "value": "Committed"}]}
```

### Step 6: Report Summary

```markdown
## Work Items Created: [Feature Name]

| # | AB# | Title | Repo | Depends On | State | Link |
|---|-----|-------|------|------------|-------|------|
| WI-1 | AB#12345 | [title] | common | — | Committed | [link] |
| WI-2 | AB#12346 | [title] | service | AB#12345 | Committed | [link] |

### Settings Used
- **Parent Feature**: AB#12340 (or "None")
- **Area Path**: `[path]`
- **Iteration**: `[path]`
- **Assigned to**: `[assignee]`

### Dispatch Order
1. Dispatch **AB#12345** first
2. After merge → dispatch **AB#12346** and **AB#12347** in parallel

### Next Step
> Say **"dispatch"** to send the first work item to Copilot coding agent.
```

## MCP Server Recovery

If ADO MCP tools fail mid-workflow:
1. Restart: Command Palette → `MCP: Restart Server` → `ado`
2. If still broken, try a **new chat session**
3. **Preserve progress**: Note which items were created (AB# IDs) so the new
   session can continue without duplicating work items
4. In the new session, the developer can say:
   > "Continue creating PBIs for [feature]. WI-1 already created as AB#12345. Create WI-2 onwards."

## Edge Cases

### Plan has a single PBI
Skip dependency linking. Create one work item and report.

### Developer wants different area paths per PBI
If PBIs target different teams or modules, ask if they want different area paths.
Present discovered options for each PBI individually.

### Developer modifies the plan before approving
If the developer asks for changes (add/remove PBIs, change descriptions), defer back
to the `feature-planner` skill to regenerate, then return here for creation.

### Creating a Parent Feature Work Item

If the developer wants a parent Feature, create it first:
```json
{
  "project": "<from config>",
  "workItemType": "Feature",
  "fields": [
    {"name": "System.Title", "value": "[Feature Name]"},
    {"name": "System.Description", "value": "<p>[Brief description]</p>", "format": "Html"},
    {"name": "System.AreaPath", "value": "[confirmed path]"},
    {"name": "System.IterationPath", "value": "[confirmed iteration]"},
    {"name": "System.AssignedTo", "value": "[confirmed assignee]"},
    {"name": "System.Tags", "value": "ai-generated"}
  ]
}
```
Record the Feature ID for parenting PBIs.
