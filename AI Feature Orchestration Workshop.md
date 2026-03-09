# AI-Driven Feature Orchestration Workshop

> **From feature idea to merged PRs — orchestrated by AI agents in VS Code.**

---

## What We Built

An end-to-end AI-driven development pipeline that takes a feature from idea to implementation:

```
Feature Idea → Design Spec → Work Item Plan → ADO Work Items → Coding Agent PRs → Merged Code
```

**The developer describes a feature. AI does the rest:**
1. **Researches** the codebase to understand existing patterns
2. **Writes** a detailed design spec with solution options
3. **Decomposes** the design into repo-targeted work items
4. **Creates** work items in Azure DevOps
5. **Dispatches** to coding agents (GitHub Copilot or ADO Copilot SWE)
6. **Monitors** agent PRs and facilitates iteration

All from VS Code. All with human approval gates at every stage.

### The Pipeline

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Design  │ →  │   Plan   │ →  │ Backlog  │ →  │ Dispatch │ →  │ Monitor  │
│          │    │          │    │          │    │          │    │          │
│ Research │    │ Decompose│    │ Create   │    │ Send to  │    │ Track    │
│ codebase │    │ into     │    │ work     │    │ coding   │    │ agent    │
│ + write  │    │ work     │    │ items in │    │ agent    │    │ PRs      │
│ spec     │    │ items    │    │ ADO      │    │          │    │          │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │               │               │               │
  askQuestion     askQuestion     askQuestion     askQuestion     Status
  for approval    for approval    for settings    for next step   table
```

---

## Architecture: The Building Blocks

Everything is built with standard VS Code customization features. No proprietary frameworks.

| Component | What it is | VS Code feature | Purpose |
|-----------|-----------|----------------|---------|
| **Skills** | Markdown in `skills/` | Agent Skills | Domain knowledge and workflows |
| **Agents** | Markdown in `agents/` | Custom Agents | Personas that orchestrate tools |
| **Commands** | Markdown files | Prompt Files / Commands | Slash commands for each pipeline stage |
| **Hooks** | JavaScript files | Agent Hooks | State management, lifecycle events |
| **MCP servers** | JSON config | MCP Protocol | Connect to external services (ADO) |

### How They Connect

```
User types: /feature-design "Add retry logic"
                │
                ▼
        Command / Prompt File
        → routes to the orchestrator agent
                │
                ▼
        Orchestrator Agent
        → delegates to specialized subagents
                │
                ▼
        Subagents (researcher, designer, planner, creator, dispatcher)
        → each reads its skill for domain knowledge
                │
                ▼
        Skills provide step-by-step workflows
        → research, write spec, decompose, create PBIs, dispatch
```

---

## Two Ways to Get Started

### Check Your VS Code Version

`Help → About` or `code --version`

| VS Code Version | Recommended Path | Time to Set Up |
|----------------|-----------------|----------------|
| **1.110+** (February 2026) | **Plugin** — zero-copy install | ~5 minutes |
| **1.109 or older** | **Starter Kit** — copy files into repo | ~15 minutes |

---

## Path A: Plugin (VS Code 1.110+)

The **Feature Orchestrator Plugin** is a pre-packaged bundle of agents, skills, commands,
and hooks that you install once and configure per-project.

### Install

1. **Get the plugin** — download [`feature-orchestrator-plugin.zip`](https://github.com/AzureAD/android-complete/blob/shahzaibj/orchestrator-workshop/feature-orchestrator-plugin.zip) from the workshop branch
2. **Unzip** to a permanent location:
   ```powershell
   Expand-Archive -Path "feature-orchestrator-plugin.zip" -DestinationPath "$HOME\feature-orchestrator-plugin"
   ```
3. **Register** in VS Code `settings.json` (`Ctrl+Shift+P` → "Preferences: Open User Settings (JSON)"):
   ```json
   "chat.plugins.enabled": true,
   "chat.plugins.paths": {
       "C:\\Users\\you\\feature-orchestrator-plugin": true
   }
   ```
4. **Reload** VS Code (`Ctrl+Shift+P` → "Developer: Reload Window")
5. **Verify** — type `/` in Copilot Chat, you should see commands like `feature-orchestrator-plugin:setup`

### Configure

6. Run **`/feature-orchestrator-plugin:setup`** in Copilot Chat

   The setup wizard guides you through 10 steps:

   | Step | What | Type |
   |------|------|------|
   | 1 | Check Prerequisites (Node.js, gh, az) | Automated |
   | 2 | Discover Repositories (git remotes) | Automated |
   | 3 | Discover Modules (build files) | Automated |
   | 4 | Project Info (name, description) | Pre-filled, confirm |
   | 5 | Confirm Repos & Modules | Review & confirm |
   | 6 | Account Discovery & Mapping (GitHub/ADO) | Auto-discover + confirm |
   | 7 | ADO Work Item Config (org, project, type) | Pre-filled, confirm |
   | 8 | Design Docs (save path, template) | Choose |
   | 9 | Generate Codebase Context (deep scan) | Optional |
   | 10 | Finalize (write config, install state CLI, configure MCP) | Automated |

   Most steps are pre-filled from auto-detection — you just confirm.

### Use

```
/feature-orchestrator-plugin:feature-design Add retry logic with exponential backoff
```

---

## Path B: Starter Kit (VS Code 1.109 or older)

The **Starter Kit** is a set of template files you copy into your repo and customize.

### Install

1. **Get the starter kit** — download from the [workshop branch](https://github.com/AzureAD/android-complete/tree/shahzaibj/orchestrator-workshop/workshop/starter-kit)
2. **Copy** the `.github/` folder into your repo root
2. **Search for `TODO: CUSTOMIZE`** in every file and update:
   - Repo slugs and module names (in the orchestrator agent)
   - ADO project name (in pbi-creator skill)
   - Design docs path (in design-author skill)
   - State directory name (in state-utils.js and orchestrator agent)
3. **Install state CLI**:
   ```powershell
   $stateDir = Join-Path $HOME ".my-project-orchestrator"  # Change to your project name
   New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
   Copy-Item ".github/hooks/state-utils.js" "$stateDir/state-utils.js"
   ```
4. **Configure MCP** — create `.vscode/mcp.json`:
   ```json
   {
     "servers": {
       "ado": {
         "type": "stdio",
         "command": "npx",
         "args": ["-y", "@azure-devops/mcp", "YOUR_ORG",
                  "-d", "core", "work", "work-items", "repositories", "pipelines"]
       }
     }
   }
   ```

### What's Included

```
.github/
├── agents/          (6 files — orchestrator + 5 subagents)
├── skills/          (6 skills — researcher, design-author, design-reviewer,
│                     feature-planner, pbi-creator, pbi-dispatcher)
├── prompts/         (5 slash commands — design, plan, backlog, dispatch, status)
└── hooks/           (state-utils.js)
```

### Use

```
/feature-design Add retry logic with exponential backoff
```

---

## The Pipeline in Action

Regardless of which path you chose, the pipeline works the same way.

### Stage 1: Design

```
/feature-design I want to add retry logic with exponential backoff to the API client
```

The orchestrator:
1. Runs the **codebase-researcher** subagent to understand existing patterns
2. Passes findings to the **design-writer** subagent
3. Design-writer creates a spec with problem, requirements, 2+ solution options, recommendation
4. Presents choices via `askQuestion`: review locally, approve, open PR, revise

### Stage 2: Plan

After design approval, the orchestrator:
1. Reads the approved design spec
2. Passes it to the **feature-planner** subagent
3. Planner decomposes into repo-targeted work items with:
   - Summary table, dependency graph, dispatch order
   - Full self-contained descriptions per work item
4. Presents plan for review

### Stage 3: Backlog

After plan approval:
1. **pbi-creator** discovers ADO defaults (area path, iteration, assignee)
2. Presents ALL settings for confirmation (⛔ mandatory — never skips)
3. Creates work items in ADO with full descriptions
4. Links dependencies and marks as Committed

### Stage 4: Dispatch

For **GitHub repos**: `gh agent-task create` sends to Copilot coding agent
For **ADO repos**: Tags work item with `copilot:repo=org/project/repo@branch` and assigns to GitHub Copilot (SWE agent)

### Stage 5: Monitor

Check PR status, present results table, iterate via `@copilot` PR comments.

---

## Key Design Decisions

### Why Skills + Agents (not one big prompt)?

- **Skills are reusable** — the codebase-researcher skill works whether called from the orchestrator or directly
- **Agents keep context clean** — each subagent starts fresh with only what it needs
- **Maintenance** — change one skill without touching the orchestrator

### Why Approval Gates?

The AI makes mistakes. Every stage boundary is a checkpoint:
- Design too broad? Revise before planning.
- PBI too large? Split before creating in ADO.
- Wrong iteration? Fix before dispatching.

Without gates, one early mistake cascades into wasted agent sessions and bad PRs.

### Why Self-Contained PBI Descriptions?

The coding agent (GitHub Copilot or ADO SWE) has NO access to:
- Your design docs
- Other repos in your workspace
- The chat conversation that created the PBI

The PBI description IS the entire implementation spec. If context is missing, the agent guesses — badly.

### Why askQuestion (not plain text)?

```
❌ "Say 'plan' to continue or 'revise' to change"
✅ askQuestion({ options: [{ label: "📋 Plan PBIs" }, { label: "✏️ Revise" }] })
```

Clickable UI prevents typos, shows available options at a glance, and creates a consistent experience.

---

## Tips for Success

1. **Start small** — Get design + plan working before adding backlog + dispatch
2. **Skills are your knowledge base** — The better they describe your codebase, the better the agent performs
3. **PBI quality = PR quality** — Invest in clear, specific, self-contained descriptions
4. **Title sanitization** — Remove colons (`:`) from ADO work item titles — they break the API
5. **Never minimal descriptions** — If PBI creation fails, fix and retry. Never fall back to summaries.
6. **Read codebase-context.md** — If you generated one during setup, the researcher uses it for deeper findings

---

## What's Available

### Plugin Commands

| Command | Description |
|---------|-------------|
| `setup` | Configure the plugin for your project |
| `feature-design` | Start a new feature with design |
| `feature-plan` | Decompose design into work items |
| `feature-backlog` | Create work items in ADO |
| `feature-dispatch` | Send to coding agent |
| `feature-status` | Check PR status |
| `feature-continue` | Resume a feature from current step |
| `feature-pr-iterate` | Review and iterate on agent PRs |

### Skills

| Skill | Purpose |
|-------|---------|
| `codebase-researcher` | Systematic codebase exploration with evidence-based findings |
| `design-author` | Write design specs with solution options and trade-offs |
| `design-reviewer` | Address inline review comments on design specs |
| `feature-planner` | Decompose into self-contained, right-sized work items |
| `pbi-creator` | Create and link work items in Azure DevOps |
| `pbi-dispatcher-github` | Dispatch to GitHub Copilot coding agent |
| `pbi-dispatcher-ado-swe` | Dispatch to ADO Copilot SWE agent |

---

## Prerequisites Checklist

- [ ] VS Code ≥ 1.109 (1.110+ for plugin support)
- [ ] GitHub Copilot extension installed and licensed
- [ ] Node.js installed (`winget install OpenJS.NodeJS.LTS`)
- [ ] GitHub CLI (`gh`) installed and authenticated (for GitHub repos)
- [ ] Azure CLI (`az`) with azure-devops extension (optional, for ADO features)
- [ ] ADO MCP Server configured (`.vscode/mcp.json`)
- [ ] `.github/copilot-instructions.md` exists in your repo

---

## Resources

- **VS Code Agent Plugins**: https://code.visualstudio.com/docs/copilot/customization/agent-plugins
- **VS Code Custom Agents**: https://code.visualstudio.com/docs/copilot/customization/custom-agents
- **VS Code Skills**: https://code.visualstudio.com/docs/copilot/customization/agent-skills
- **VS Code Prompt Files**: https://code.visualstudio.com/docs/copilot/customization/prompt-files
- **VS Code Agent Hooks**: https://code.visualstudio.com/docs/copilot/customization/hooks
- **MCP Servers**: https://code.visualstudio.com/docs/copilot/customization/mcp-servers
- **Awesome Copilot Plugins**: https://github.com/github/awesome-copilot
