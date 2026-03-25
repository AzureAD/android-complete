---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Start a new feature: research the codebase and create a design spec"
---

# Design Phase

You are in the **Design** phase. The user will describe a feature below.

**First**: Read `.github/orchestrator-config.json` for project configuration.

**Step 0**: Register the feature in state:
```powershell
$su = Join-Path $HOME ".feature-orchestrator" "state-utils.js"
node $su add-feature '{"name": "<short feature name>", "step": "designing"}'
```

**Step 1**: Use the `codebase-researcher` skill to understand existing patterns.
Instruct it to return **comprehensive, detailed output** — your design depends on its findings.

**Step 2**: Write a design spec covering:
- Problem description and business context
- Requirements (functional + non-functional)
- Solution options (at least 2) with pseudo code and pros/cons
- Recommended solution with reasoning
- API surface changes (if applicable)
- Data flow across components
- Feature flag strategy
- Testing strategy
- Cross-repo impact

Save to the configured `design.docsPath` location.

**Step 3**: Present the design using `askQuestion`:
```
askQuestion({
  question: "Design spec is ready. What would you like to do?",
  options: [
    { label: "📖 Review locally", description: "Open in editor for inline review" },
    { label: "✅ Approve & plan PBIs", description: "Move to work item planning" },
    { label: "📋 Open draft PR", description: "Push as draft for team review" },
    { label: "✏️ Revise design", description: "Make changes first" }
  ]
})
```

**Pipeline**: 📝 **Design** → ○ Plan → ○ Backlog → ○ Dispatch → ○ Monitor
