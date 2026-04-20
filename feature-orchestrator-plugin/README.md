# Feature Orchestrator Plugin

AI-driven feature development pipeline for GitHub Copilot. Automates the full lifecycle:

**Design → Plan → Backlog → Dispatch → Monitor**

1. **Design** — Research the codebase, write a design spec with solution options
2. **Plan** — Decompose the approved design into right-sized, repo-targeted work items
3. **Backlog** — Create work items in Azure DevOps with proper dependencies
4. **Dispatch** — Send work items to GitHub Copilot coding agent for implementation
5. **Monitor** — Track agent PRs and iterate on feedback

## Installation

### From VS Code

1. Open the Extensions sidebar (`Ctrl+Shift+X`)
2. Search for `@agentPlugins` and browse available plugins
3. Install **feature-orchestrator**

### Local Installation (for development)

```jsonc
// In your VS Code settings.json:
"chat.plugins.paths": {
    "/path/to/feature-orchestrator-plugin": true
}
```

## Setup

After installing, configure the plugin for your project:

1. Open a chat and run: `/feature-orchestrator-plugin:setup`
2. The setup wizard guides you through:
   - **Project info** — name and description
   - **Repository mapping** — which modules map to which GitHub repos
   - **Azure DevOps** — organization, project, work item type
   - **Design docs** — where to store design specs
   - **Prerequisites** — checks for `gh` CLI, `node`, authentication

This creates `.github/orchestrator-config.json` in your workspace. Commit it to share with your team.

## Quick Start

After setup, describe a feature to start the pipeline:

```
/feature-orchestrator-plugin:feature-design I want to add retry logic with exponential backoff to the API client
```

Or use the agent directly:
```
@feature-orchestrator-plugin:feature-orchestrator.agent Add push notification support for auth state changes
```

## Commands

| Command | Description |
|---------|-------------|
| `/setup` | Configure the plugin for this project |
| `/feature-design` | Start a new feature — research + design spec |
| `/feature-plan` | Decompose approved design into work items |
| `/feature-backlog` | Create work items in Azure DevOps |
| `/feature-dispatch` | Send work items to Copilot coding agent |
| `/feature-status` | Check agent PR status |
| `/feature-continue` | Resume a feature from its current step |
| `/feature-pr-iterate` | Review and iterate on agent PRs |

(All commands are prefixed with `feature-orchestrator-plugin:` in the UI)

## Skills

| Skill | Description |
|-------|-------------|
| `codebase-researcher` | Systematic codebase exploration with evidence-based findings |
| `design-author` | Write detailed design specs with solution options and trade-offs |
| `design-reviewer` | Address inline review comments on design specs |
| `feature-planner` | Decompose features into self-contained, right-sized work items |
| `pbi-creator` | Create and link work items in Azure DevOps |
| `pbi-dispatcher` | Dispatch work items to Copilot coding agent |

## Configuration

The plugin uses `.github/orchestrator-config.json` for project-specific settings:

```jsonc
{
  "project": {
    "name": "My Project",
    "description": "Brief description"
  },
  "repositories": {
    "core-repo": {
      "slug": "my-org/core-repo",
      "host": "github",
      "baseBranch": "main"
    },
    "api-repo": {
      "slug": "my-org/api-repo",
      "host": "github",
      "baseBranch": "dev",
      "accountType": "emu"
    }
  },
  "modules": {
    "core": { "repo": "core-repo", "path": "core/", "purpose": "Shared utilities and data models" },
    "api": { "repo": "core-repo", "path": "api/", "purpose": "Public API surface" },
    "service": { "repo": "api-repo", "purpose": "Backend service" }
  },
  "ado": {
    "org": "my-org",
    "project": "Engineering",
    "workItemType": "Product Backlog Item",
    "iterationDepth": 6
  },
  "design": {
    "docsPath": "docs/designs/",
    "templatePath": null
  }
}
```

### Per-Developer Config

GitHub account mappings are stored per-developer (gitignored):

`.github/developer-local.json`:
```json
{
  "github_accounts": {
    "public": "your-github-username",
    "emu": "your-emu-username"
  }
}
```

## Prerequisites

- **VS Code** 1.109+ with GitHub Copilot
- **GitHub CLI** (`gh`) — for dispatching and PR monitoring
- **Node.js** — for state management (`state-utils.js` installed to `~/.feature-orchestrator/`)
- **Azure DevOps MCP Server** — for work item management (optional but recommended)

## Architecture

```
Plugin
├── agents/              # Orchestrator agent (conductor)
├── commands/            # Slash commands with agent routing
├── skills/              # Specialized skills for each phase
│   ├── codebase-researcher/
│   ├── design-author/
│   ├── design-reviewer/
│   ├── feature-planner/
│   │   └── references/pbi-template.md
│   ├── pbi-creator/
│   └── pbi-dispatcher/
├── hooks/               # State management CLI
├── schemas/             # Config JSON schema
└── .mcp.json            # MCP server configuration
```

## License

MIT
