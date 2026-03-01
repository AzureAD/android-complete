# Feature Orchestrator Extension

VS Code Chat Participant (`@orchestrator`) for AI-driven feature development across the Android Auth multi-repo project.

## Usage

In VS Code Copilot Chat (Agent Mode):

```
@orchestrator implement IPC retry logic for broker communication
```

Or use individual commands:

```
@orchestrator /design add retry logic to IPC calls
@orchestrator /plan
@orchestrator /dispatch
@orchestrator /status
```

## Flow

1. `@orchestrator <feature description>` — runs the full flow: design → plan → dispatch
2. `@orchestrator /design <prompt>` — just write a design spec
3. `@orchestrator /plan` — break approved design into PBIs (uses ADO MCP)
4. `@orchestrator /dispatch` — dispatch PBIs to Copilot coding agent
5. `@orchestrator /status` — check agent PR status across repos

## Development

```bash
cd extensions/feature-orchestrator
npm install
npm run compile
```

Then press F5 to launch the Extension Development Host.

## Architecture

- `participant.ts` — Chat Participant handler, routes commands to workflow steps
- `workflow.ts` — State machine: idle → designing → design_review → planning → plan_review → dispatching → monitoring → done
- `skills.ts` — Reads `.github/skills/` files and builds LLM prompts
- `tools.ts` — Wrappers for `gh` CLI, account switching, agent dispatch
