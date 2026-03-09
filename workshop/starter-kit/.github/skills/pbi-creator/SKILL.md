---
name: pbi-creator
description: Create work items in Azure DevOps from a feature plan.
---

# PBI Creator

Create Azure DevOps work items from an approved feature plan.

## Prerequisites
- ADO MCP Server must be running

## Workflow

### Step 1: Parse the Plan
Extract titles, descriptions, dependencies, tags from the plan.

### Step 2: Discover ADO Defaults
1. Call `mcp_ado_wit_my_work_items` for recent items
2. Extract area paths, iterations, assignee
3. Call `mcp_ado_work_list_iterations` with `depth: 6`
4. **Filter to current month or future only**

### Step 3: Present Options

## ⛔ HARD STOP — DO NOT SKIP

**Ask ALL 4 questions BEFORE creating any work items:**
- Area path
- Iteration (current/future only)
- Assignee
- Parent feature

**Batch into a SINGLE `askQuestion` call.** Wait for ALL answers.

### Step 4: Create Work Items

<!-- TODO: CUSTOMIZE — Update project name -->
Use `mcp_ado_wit_create_work_item` with `project: "Engineering"`.

### ⚠️ Title Sanitization
**Remove colons (`:`) from titles** — they break the ADO API.
Use em-dash (`—`) instead.

### ⚠️ NEVER Create With Minimal Descriptions
Every work item MUST include the FULL description. If the tool fails,
retry with sanitized input. **NEVER fall back to minimal descriptions.**

### ⚠️ ADO Org/Project
Pass **plain names only**, never URLs. Extract org/project from any URL provided.

### Step 5: Link Dependencies + Parent
1. Replace WI-N with AB#[id] in descriptions
2. Link dependencies via `mcp_ado_wit_work_items_link`
3. Parent to Feature if created

### Step 6: Mark as Committed + Report Summary
