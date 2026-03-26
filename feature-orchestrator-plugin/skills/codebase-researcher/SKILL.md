---
name: codebase-researcher
description: Systematically explore codebases to find implementations, patterns, and architecture. Use for "where is X implemented", "how does Y work", "trace the flow of", or any request requiring codebase exploration with evidence-based findings.
---

# Codebase Researcher

Explore this codebase systematically with evidence-based findings.

## Project Knowledge

Read `.github/copilot-instructions.md` for project-wide conventions and coding standards.

## Repository Structure

Discover the repository structure by exploring the workspace — check for modules,
sub-directories with their own build files, and README files.

| Module | Purpose | Key Paths |
|--------|---------|-----------|
| *Discover by exploring the workspace* | | |

**⚠️ CRITICAL: Always search across ALL modules/directories.** Code is often shared or duplicated.

## Core Principles

1. **Never guess** — Only report what is actually found in the repo
2. **Always cite sources** — Every finding must include file path and line numbers
3. **Acknowledge gaps** — Explicitly state when something cannot be found
4. **Rate confidence** — Assign HIGH/MEDIUM/LOW to each finding
5. **Search all modules** — Check every relevant directory for each query

## Research Workflow

### Step 1: Understand the Target

Clarify what to find:
- Feature/concept name
- Which layer (client, service, shared, etc.)
- Expected patterns (class names, function signatures)

### Step 2: Search Strategy

Execute searches in this order, **always searching across all modules**:

1. **Semantic search** — Start with natural language query
2. **Grep search** — Exact patterns, class names, error codes
3. **File search** — Find by naming convention (e.g., `**/*Operation*.kt`)
4. **Directory exploration** — List relevant directories in each module
5. **Read files** — Confirm findings with actual code

### Step 3: Trace Call Chains

For the feature area being researched, trace the complete flow:
- Identify the entry point
- Follow across module boundaries
- Note threading model and error handling at each boundary

### Step 4: Identify Invariants

Search for constraints that govern the affected code:
- Threading annotations, synchronization
- Serialization contracts, protocol versions
- Lifecycle dependencies, feature flags

### Step 5: Validate Findings

For each potential finding:
- Read the actual code (don't rely only on search snippets)
- Identify which module it belongs to
- Note the exact location (file + line range)
- Assess confidence level

### Step 6: Report Results

```markdown
## Research: [Topic]

### Findings

#### Finding 1: [Brief description]
- **Module**: [which module]
- **File**: [path/to/file.ext](path/to/file.ext#L10-L25)
- **Confidence**: HIGH | MEDIUM | LOW
- **Evidence**: [What makes this the right code]

[Code snippet if helpful]

#### Finding 2: ...

### Unknowns & Risk Areas

- [Thing searched for but not found]
- Search attempts: [what was tried]
- [Areas that might be affected but couldn't confirm]

### Suggested Next Steps

- [Additional areas to explore]
- [Related code that might be relevant]
```

## Confidence Levels

| Level | Criteria |
|-------|----------|
| **HIGH** | Exact match. Code clearly implements the feature. Names match. |
| **MEDIUM** | Likely match. Code appears related but naming differs or implementation is partial. |
| **LOW** | Possible match. Found tangentially related code, or inference required. |

## Data Flow Investigation

When asked about **what data is returned**, **how data flows**, or **what happens to data**:

1. **Find the Data Structure** — Confirm the field exists, check serialization
2. **Find Construction/Population Code** — Search for Builder/factory methods
3. **Check Conditional Logic** — Search for `if` statements, feature flag checks, version checks
4. **Trace the Complete Flow** — Follow from entry → processing → response → return

### Flow Investigation Pitfalls

❌ Don't stop after finding a field definition — check actual behavior
❌ Don't assume data flows unchanged — check for filtering/transformation
❌ Don't ignore version/flag checks — behavior often changes based on these
✅ Search for Builder usage and construction patterns
✅ Look for Adapter/Converter classes in the flow
✅ Check for conditional logic based on configuration or feature flags

## Anti-Patterns to Avoid

| Anti-Pattern | Problem | Correct Approach |
|--------------|---------|------------------|
| Searching only one module | Miss cross-module code | Search ALL modules |
| "This is likely in..." | Speculation without evidence | Search first, report only found |
| Path without line numbers | Imprecise, hard to verify | Always include line numbers |
| Stopping at definition | Misses conditional logic | Trace to construction/adapter |
| Brief summary | Loses detail for next step | Be thorough and comprehensive |
