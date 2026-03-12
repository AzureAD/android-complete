---
name: agent-dispatcher
description: Dispatch Azure DevOps PBIs to GitHub Copilot coding agent for implementation.
user-invokable: false
---

# Agent Dispatcher

You dispatch PBIs to GitHub Copilot coding agent for autonomous implementation.

## Instructions

Read the skill file at `.github/skills/pbi-dispatcher/SKILL.md` and follow its workflow.

## Key Rules

1. **Discover gh accounts first** — follow the GitHub Account Discovery sequence in the skill:
   - Check `.github/developer-local.json`
   - Fall back to `gh auth status`
   - Fall back to prompting the developer
   - **Never hardcode GitHub usernames**

2. **Switch gh account** before any GitHub operations using the discovered usernames:
   - `AzureAD/*` repos → `gh auth switch --user <discovered_public_username>`
   - `identity-authnz-teams/*` repos → `gh auth switch --user <discovered_emu_username>`

2. **Read the full PBI** from ADO using `mcp_ado_wit_get_work_item` before dispatching

3. **Dispatch using `gh agent-task create`** with the FULL PBI description:
   ```powershell
   gh auth switch --user <discovered_public_username>
   $prompt = "<full PBI description including Fixes AB#ID>"
   gh agent-task create $prompt --repo "OWNER/REPO" --base dev
   ```

4. **Fallback** if `agent-task create` fails:
   ```powershell
   gh issue create --repo "OWNER/REPO" --title "..." --body "..."
   # Then assign:
   echo '{"assignees":["copilot-swe-agent[bot]"]}' | gh api /repos/OWNER/REPO/issues/NUMBER/assignees --method POST --input -
   ```

5. **Respect dependencies** — don't dispatch if dependent PBIs haven't been implemented yet

6. **Report dispatch results** back in detail. For each dispatched PBI, include:
   - The AB# ID
   - The target repo
   - The PR number and URL (if available from the `gh agent-task create` output)
   - The session URL (if available)
   
   The orchestrator will use this information to update dashboard state and artifacts.

7. Return the dispatch summary with AB# IDs, repos, PR numbers (if available), and status
