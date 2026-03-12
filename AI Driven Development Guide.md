# AI-Driven Development Guide

This guide covers the AI-driven feature development system for the Android Auth multi-repo project. It automates the full lifecycle: **Design → Plan → Backlog → Dispatch → Monitor**.

## Quick Start

### 1. Run the setup script

```powershell
.\scripts\setup-ai-orchestrator.ps1
```

This checks/installs all prerequisites, configures GitHub accounts, clones design-docs, and builds + installs the Feature Orchestrator VS Code extension.

### 2. Reload VS Code

After setup completes, press `Ctrl+Shift+P` → "Reload Window".

### 3. Start a feature

Either:
- Click the **+** button in the Feature Orchestrator sidebar (rocket icon in the activity bar)
- Type `/feature-design <description>` in Copilot Chat
- Switch to the **feature-orchestrator** agent in the agents dropdown and describe your feature

---

## The Pipeline

Every feature goes through these stages:

```
Design → Plan → Backlog → Dispatch → Monitor
```

| Stage | What happens | Who does the work |
|-------|-------------|-------------------|
| **Design** | Researches codebase, writes a design spec, opens ADO PR for review | AI (codebase-researcher + design-writer agents) |
| **Plan** | Decomposes the approved design into repo-targeted PBIs | AI (feature-planner agent) |
| **Backlog** | Creates PBIs as work items in Azure DevOps | AI (pbi-creator agent) + you (confirming area path, iteration, assignee) |
| **Dispatch** | Sends PBIs to GitHub Copilot coding agent for implementation | AI (agent-dispatcher) |
| **Monitor** | Tracks agent PRs, reviews code, iterates on feedback | You + AI |

### Approval Gates

The pipeline **stops between stages** and asks for your approval before proceeding. You stay in control — the AI proposes, you decide.

---

## Prompt Commands

Type these in Copilot Chat to invoke each stage:

| Command | Purpose |
|---------|---------|
| `/feature-design <description>` | Start a new feature — research + design spec |
| `/feature-plan` | Decompose approved design into PBIs |
| `/feature-backlog` | Create PBIs in Azure DevOps |
| `/feature-dispatch` | Dispatch PBIs to Copilot coding agent |
| `/feature-status` | Check tracked PR statuses |
| `/feature-continue` | Resume from current pipeline step |
| `/feature-pr-iterate` | Review a PR, analyze comments, send feedback to `@copilot` |

---

## The Dashboard

The Feature Orchestrator sidebar shows:

- **Metrics** — active/completed features, PBI count, PRs merged
- **Active Features** — cards with progress dots and action buttons
- **Completed Features** — archived features with all artifacts
- **My Open PRs** — your PRs + Copilot agent PRs across all repos

Click any feature card to open the **Feature Detail Panel**.

### Feature Detail Panel

Shows everything about a feature in one place:

- **Pipeline progress** — visual stage tracker
- **Phase durations** — how long each stage took
- **Design Spec** — link to doc + ADO PR
- **PBI table** — order, dependencies, status, dispatch button (🚀)
- **Agent PRs** — status, comments, iterate (💬) and checkout (📥) buttons
- **Manual entry** — add design specs, PBIs, or PRs via + buttons
- **Live refresh** — fetches latest PBI status from ADO and PR status from GitHub

---

## Working with Agent PRs

When the Copilot coding agent creates a PR:

### Review

1. Click **📥** (Checkout) in the detail panel to get the branch locally
2. Review the code in VS Code
3. Click **💬** (Iterate) to analyze reviewer comments and send feedback

### Iterate

The `/feature-pr-iterate` command:
1. Fetches the PR diff and all review comments
2. Presents options: delegate to `@copilot`, analyze first, or approve
3. If analyzing: shows each reviewer comment with a proposed resolution
4. Posts `@copilot` comments on the PR to trigger another round of agent coding

### Auto-Completion

When all PBIs for a feature are resolved/done:
- The feature automatically moves to "Complete"
- You get a notification: "🎉 Feature is complete!"

---

## Manual Artifact Entry

Not everything has to go through the AI pipeline. You can manually register:

- **Design specs** — Browse for a local file or paste an ADO PR URL
- **PBIs** — Enter an AB# ID (auto-fetches title and status from ADO)
- **PRs** — Enter repo + PR number (auto-fetches from GitHub)

Use the **+** buttons on each section header in the detail panel.

---

## State Management

Feature state is stored at `~/.android-auth-orchestrator/state.json` (not in the repo — each developer has their own state).

### CLI Commands

The AI agent calls these automatically during the pipeline — you don't need to run them
yourself. They're documented here for reference and manual troubleshooting.

```powershell
# List all features
node .github/hooks/state-utils.js list-features

# Get full feature details
node .github/hooks/state-utils.js get-feature "<feature name>"

# Update step
node .github/hooks/state-utils.js set-step "<feature name>" <step>

# Add artifacts
node .github/hooks/state-utils.js set-design "<feature name>" '{"docPath":"...","status":"approved"}'
node .github/hooks/state-utils.js add-pbi "<feature name>" '{"adoId":123,"title":"...","module":"common","status":"Committed"}'
node .github/hooks/state-utils.js add-agent-pr "<feature name>" '{"repo":"common","prNumber":123,"prUrl":"...","status":"open"}'
```

**Note**: Use single quotes for JSON args in PowerShell.

---

## Prerequisites

The setup script handles all of these, but for reference:

| Requirement | Purpose | Required? |
|------------|---------|-----------|
| VS Code ≥ 1.109 | Agent mode, skills, prompt files, askQuestion | **Yes** |
| GitHub Copilot extension | Chat, agents | **Yes** |
| Node.js | Hooks, state management, extension build | **Yes** |
| GitHub CLI (`gh`) | Dispatch, PR status, checkout | **Yes** |
| Azure CLI (`az`) + devops extension | Live PBI status refresh | Optional |
| `design-docs/` repo | Design spec authoring + PR creation | Recommended |

### GitHub Authentication

You need two GitHub accounts authenticated via `gh`:
- **Public** (e.g., `johndoe`) — for AzureAD/* repos (common, msal, adal)
- **EMU** (e.g., `johndoe_microsoft`) — for identity-authnz-teams/* repos (broker)

The setup script discovers logged-in accounts and saves them to `.github/developer-local.json`.

---

## Works Without the Extension

The entire pipeline works without the VS Code extension installed. Prompt files, agents, hooks, and state management are all independent. The extension only provides:
- Dashboard sidebar with metrics and feature cards
- Feature detail panel with artifact tracking
- Local design spec review (inline comments via gutter icons + the **design-reviewer** skill that reads and addresses your comments)
- Live refresh, auto-completion detection

Without it, you use `/feature-design`, `/feature-plan`, etc. directly in chat. The AI agent manages state automatically via the CLI.

---

## Troubleshooting

| Issue | Solution |
|-------|---------|
| `/feature-design` not recognized | Ensure VS Code ≥ 1.109 and check `.github/prompts/` folder exists |
| Agent doesn't follow orchestrator instructions | Check `.github/agents/feature-orchestrator.agent.md` is present |
| State commands return "Feature not found" | Check feature name matches exactly (case-insensitive) |
| Dashboard shows wrong step | Click ↻ Refresh or check `~/.android-auth-orchestrator/state.json` |
| `gh agent-task create` fails | Verify `gh auth status` shows both accounts authenticated |
| PBI status not updating | Ensure `az` CLI is installed and authenticated (`az login`) |
| Extension not loading | Run `.\scripts\setup-ai-orchestrator.ps1` to rebuild |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    VS Code Extension                     │
│  Dashboard │ Feature Detail │ Design Review │ State Mgmt │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────┴───────────────────────────────────┐
│                    Prompt Files                          │
│  /feature-design │ /feature-plan │ /feature-status │ ... │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────────┐
│              feature-orchestrator Agent                 │
│         Conductor — delegates to subagents              │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   Researcher    Design Writer   Planner
        │              │              │
   PBI Creator   Dispatcher    (skills)
```

### Key Files

| Component | Location |
|-----------|----------|
| Setup script | `scripts/setup-ai-orchestrator.ps1` |
| Agents | `.github/agents/*.agent.md` |
| Skills | `.github/skills/*/SKILL.md` |
| Prompt files | `.github/prompts/*.prompt.md` |
| Hooks | `.github/hooks/orchestrator.json` |
| State CLI | `.github/hooks/state-utils.js` |
| Extension source | `extensions/feature-orchestrator/src/` |
| MCP config | `.vscode/mcp.json` |
| Project instructions | `.github/copilot-instructions.md` |
