# Feature Orchestrator Extension

VS Code extension for the AI-driven feature development pipeline. Provides the dashboard UI,
feature detail panel, and design review system.

For the full developer guide, see `AI Driven Development Guide.md` in the repository root.

## What This Extension Provides

- **Dashboard sidebar** (rocket icon) — metrics, active/completed features, open PRs
- **Feature detail panel** — artifacts, PBI table with dispatch buttons, PR actions, phase durations
- **Design review system** — inline comments via gutter icons + status bar submit button
- **Manual artifact entry** — add design specs, PBIs, or PRs via + buttons
- **Live refresh** — fetches latest PBI status from ADO and PR status from GitHub
- **Auto-completion** — detects when all PBIs are resolved and marks feature as done

## Installation

Run the setup script (builds and installs automatically):
```powershell
.\scripts\setup-ai-orchestrator.ps1
```

Or build manually:
```bash
cd extensions/feature-orchestrator
npm install
npm run compile
npx @vscode/vsce package --no-dependencies --allow-missing-repository --baseContentUrl . --baseImagesUrl .
code --install-extension feature-orchestrator-latest.vsix --force
```

## Architecture

| File | Purpose |
|------|---------|
| `extension.ts` | Entry point — registers dashboard, commands |
| `dashboard.ts` | Sidebar webview — feature cards, metrics, open PRs |
| `featureDetail.ts` | Detail panel — artifacts, durations, dispatch, iterate, checkout |
| `designReview.ts` | CodeLens-based design review commenting |
| `tools.ts` | CLI helpers — `runCommand`, `switchGhAccount` |

## State

Feature state is stored at `~/.feature-orchestrator/<project>/state.json` (per-developer, not in repo).
Managed by `.github/hooks/state-utils.js`.

## Works Without This Extension

The entire pipeline (agents, prompt files, hooks, state) works without this extension.
It only provides the visual dashboard and review UI.
