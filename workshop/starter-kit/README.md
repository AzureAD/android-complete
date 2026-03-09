# AI Feature Orchestrator — Starter Kit

Copy these files into your repo's `.github/` folder to set up AI-driven feature development.

> **Note**: If you're on VS Code 1.110+ (February 2026 release), use the
> **Feature Orchestrator Plugin** instead — it's zero-copy and configurable.
> This starter kit is for older VS Code versions that don't support agent plugins.

## Setup

1. **Copy** the `.github/` folder into your repo root
2. **Customize** — search for `TODO: CUSTOMIZE` in every file and update:
   - Repo slugs and module names
   - ADO project name
   - Design docs path
   - State directory name
3. **Install state CLI** — copy `state-utils.js` to your state directory:
   ```powershell
   # TODO: Change directory name to match your project
   $stateDir = Join-Path $HOME ".my-project-orchestrator"
   New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
   Copy-Item ".github/hooks/state-utils.js" "$stateDir/state-utils.js"
   ```
4. **Configure MCP** — add ADO MCP server to `.vscode/mcp.json`:
   ```json
   {
     "servers": {
       "ado": {
         "type": "stdio",
         "command": "npx",
         "args": ["-y", "@azure-devops/mcp", "YOUR_ORG", "-d", "core", "work", "work-items", "repositories", "pipelines"]
       }
     }
   }
   ```

## What's Included

```
.github/
├── agents/                        # Agent definitions
│   ├── feature-orchestrator.agent.md  # Main conductor
│   ├── codebase-researcher.agent.md
│   ├── design-writer.agent.md
│   ├── feature-planner.agent.md
│   ├── pbi-creator.agent.md
│   └── agent-dispatcher.agent.md
├── skills/                        # Skill workflows
│   ├── codebase-researcher/SKILL.md
│   ├── design-author/SKILL.md
│   ├── design-reviewer/SKILL.md
│   ├── feature-planner/SKILL.md
│   │   └── references/pbi-template.md
│   ├── pbi-creator/SKILL.md
│   └── pbi-dispatcher/SKILL.md
├── prompts/                       # Slash commands
│   ├── feature-design.prompt.md
│   ├── feature-plan.prompt.md
│   ├── feature-backlog.prompt.md
│   ├── feature-dispatch.prompt.md
│   └── feature-status.prompt.md
└── hooks/
    └── state-utils.js             # State management CLI
```

## Usage

Use slash commands in Copilot Chat:

| Command | Description |
|---------|-------------|
| `/feature-design` | Start a new feature with design |
| `/feature-plan` | Decompose design into work items |
| `/feature-backlog` | Create work items in ADO |
| `/feature-dispatch` | Send to coding agent |
| `/feature-status` | Check PR status |

Or describe a feature directly to `@feature-orchestrator`.
