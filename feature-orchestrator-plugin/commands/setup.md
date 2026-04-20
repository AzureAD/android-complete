---
agent: feature-orchestrator-plugin:feature-orchestrator.agent
description: "Configure the Feature Orchestrator for this project (first-time setup)"
---

# Setup — Feature Orchestrator Configuration

Guide the user through setting up `.github/orchestrator-config.json` for this project.

**Check first**: Does `.github/orchestrator-config.json` already exist?
- If yes, read it and ask: "Configuration already exists. Would you like to update it or start fresh?"
- If no, proceed with setup.

## General Rules

- Output each step heading as a **separate markdown line** before any question UI.
- **askQuestion rule**: When using `allowFreeformInput: true`, do NOT also include an
  option like "Enter a different answer" or "Type custom value" — the `allowFreeformInput`
  already adds a freeform text field automatically. Adding an explicit option for it creates
  a confusing duplicate where one is clickable-but-not-typeable and the other is typeable.
- Follow the steps **sequentially** — do not skip ahead or reference later steps.

---

### Step 1: Check Prerequisites

Tell the user:
> "Let me check that the required tools are installed before we begin. This ensures
> we don't go through the full setup only to find out something is missing."

```powershell
node --version     # Node.js (for state-utils) — REQUIRED
gh --version       # GitHub CLI — only needed if any repos are on GitHub
az --version       # Azure CLI — only needed if using ADO work items
```

**Node.js** is always required (for state tracking). If missing:
> "Node.js is required for the orchestrator. Install:
> `winget install OpenJS.NodeJS.LTS` (Windows) or `brew install node` (macOS) or https://nodejs.org"
**This is a blocker** — do not proceed until Node.js is available.

**GitHub CLI** — note whether installed. Don't check repos yet (discovered in Step 2).
- If installed → note version, proceed
- If not installed → note it; will revisit in Step 6 if GitHub repos are found

**Azure CLI** — note whether installed.
- If installed → note version, proceed
- If not installed → note it's optional:
  > "Azure CLI is optional but recommended for live PBI status updates.
  > Install: `winget install Microsoft.AzureCLI` (Windows) or `brew install azure-cli` (macOS)"

Present a quick summary:
```markdown
### Prerequisites
- **Node.js**: v24.14.0 ✅
- **GitHub CLI**: v2.87.3 ✅
- **Azure CLI**: v2.82.0 ✅ (or ⚠️ Not installed — optional)
```

---

### Step 2: Discover Repositories

Tell the user:
> "Now let me discover your project's repositories by checking git remotes."

Find all distinct git remotes in the workspace:

1. Check if the workspace root has subdirectories with their own `.git` (submodules/sub-repos):
   ```powershell
   Get-ChildItem -Directory | ForEach-Object { git -C $_.Name remote get-url origin 2>$null }
   ```
2. Also check the workspace root's own remote: `git remote get-url origin`
3. Deduplicate — multiple directories may share the same remote (same repo).

**Parse each remote URL into a slug:**
- GitHub: `https://github.com/org/repo.git` → `org/repo`
- GitHub SSH: `git@github.com:org/repo.git` → `org/repo`
- ADO: `https://org@dev.azure.com/org/project/_git/repo` → `org/project/repo`
- ADO: `https://org.visualstudio.com/.../project/_git/repo` → `org/project/repo`
  (Both `dev.azure.com` and `visualstudio.com` URLs are ADO — treat them identically.
  **Do NOT label either as "legacy" or "new"** when presenting to the user — just show `ADO`.)

**Auto-detect base branch** for each repo:
```powershell
git -C <path> symbolic-ref refs/remotes/origin/HEAD 2>$null
# Falls back to checking git branch -r for origin/main or origin/dev
```

---

### Step 3: Discover Modules

Tell the user:
> "Now let me check for modules inside each repository."

For each repo, discover the logical modules inside it. Detection is language-agnostic —
look for common project structure signals:

| Signal | What it means |
|--------|---------------|
| `settings.gradle` / `build.gradle` with `include` | Android/JVM multi-module (Gradle) |
| `pom.xml` with `<modules>` | Java multi-module (Maven) |
| `package.json` in subdirectories / `workspaces` field | Node.js/JS monorepo |
| `go.work` or multiple `go.mod` files | Go multi-module workspace |
| `Cargo.toml` with `[workspace]` members | Rust workspace |
| `*.csproj` / `*.sln` with multiple projects | .NET solution |
| `pyproject.toml` / `setup.py` in subdirectories | Python multi-package |
| Directories with their own `src/` or `lib/` | Generic convention |
| Single `src/` at root, no sub-projects | Single-module repo |

If a repo has only one module, the module name defaults to the repo name.
If a repo has multiple modules, list each with its path relative to the repo root.

Also read project metadata for Step 4 (project info):
- `README.md` — first `#` heading and first paragraph
- `package.json` — `name` and `description` fields
- `build.gradle` / `settings.gradle` — `rootProject.name`
- `pom.xml` — `<name>` and `<description>`
- `Cargo.toml` — `[package]` name
- Workspace directory name as fallback

---

### Step 4: Project Info

Tell the user:
> "I need some basic info about your project. This is used in work item titles,
> design spec headers, and dashboard labels."

**Always provide a recommended name and description** from metadata discovered in Step 3.
Use the best source available (README heading, package.json, build.gradle, directory name
as last resort).

```
askQuestion({
  questions: [
    {
      header: "Project Name",
      question: "What's the short name for this project?",
      options: [
        { label: "<discovered-name>", description: "From README / build config", recommended: true }
      ],
      allowFreeformInput: true
    },
    {
      header: "Project Description",
      question: "One-line description of the project:",
      options: [
        { label: "<discovered-description>", description: "From README / build config", recommended: true }
      ],
      allowFreeformInput: true
    }
  ]
})
```

---

### Step 5: Confirm Repos & Modules

Tell the user:
> "Here's what I found. Please confirm it's correct — you can ask me to make
> corrections if anything looks wrong."

Present the results in a **readable list format** (tables get squeezed in chat
when columns have long content — use a list instead):

```markdown
## Detected Repositories & Modules

### 1. org/common-repo
- **Host**: GitHub | **Branch**: dev
- **Modules**: core, api, shared-utils

### 2. org/service-repo
- **Host**: ADO | **Branch**: main
- **Modules**: service, worker

### 3. org/client-repo
- **Host**: GitHub | **Branch**: dev
- **Modules**: client
```

If a repo has many modules (>10), group or summarize them:
> "**Modules** (25 detected): app, CoreLibrary, SharedUtils, ... and 22 more.
> See full list below."

Then use `get_confirmation`:
```
get_confirmation({
  message: "Does this repository and module mapping look correct?",
  confirmLabel: "Looks good",
  denyLabel: "I need to make corrections"
})
```

If denied, ask what to change (add/remove repos, fix slugs, rename modules, etc.),
rebuild the list, and confirm again. Repeat until confirmed.

**Concepts:**
- **Repositories**: Where code lives (GitHub or ADO (Azure DevOps)).
- **Modules**: Logical components within a repo. A single repo can have multiple modules.

The config stores repos and modules separately:
```json
"repositories": {
  "common-repo": { "slug": "org/common-repo", "host": "github", "baseBranch": "dev" },
  "service-repo": { "slug": "org/project/service-repo", "host": "ado", "baseBranch": "main" }
},
"modules": {
  "core": { "repo": "common-repo", "path": "core/", "purpose": "Shared utilities" },
  "api": { "repo": "common-repo", "path": "api/", "purpose": "Public API surface" },
  "service": { "repo": "service-repo", "purpose": "Backend processing" }
}
```

Work items reference a **module name** → lookup `modules.<name>.repo` → lookup
`repositories.<repo>` for slug, branch, and account type.

---

### Step 6: Discover & Configure Accounts

Tell the user:
> "Now I'll check your authentication. The orchestrator needs to know which accounts
> to use when dispatching work to each repo and creating work items."

**Check which sub-steps are needed** based on confirmed repos from Step 5:
- If ALL repos are on ADO (Azure DevOps) → **skip GitHub account discovery entirely**
- If ALL repos are on GitHub → **skip ADO account discovery entirely**
- If repos are mixed → do both

#### GitHub Account Discovery (skip if no GitHub repos)

**If `gh` CLI is not installed** (from Step 1), tell the user:
> "Skipping GitHub account setup — `gh` CLI is not installed. Install it later to
> enable dispatch and PR monitoring for your GitHub repos."
Then offer to install:
- Windows: `winget install --id GitHub.cli -e`
- macOS: `brew install gh`
- If user declines: warn that dispatch won't work for GitHub repos.

**If `gh` is installed**, determine how many accounts are likely needed:

1. **Collect the distinct GitHub orgs** from confirmed repos (e.g., `AzureAD`, `microsoft`)
2. **Estimate minimum accounts needed**:
   - Same org for all repos → likely 1 account
   - Multiple orgs → different orgs often mean different accounts

Discover logged-in accounts:
```powershell
$ghStatus = gh auth status 2>&1
```

**If accounts are found**, present them and proceed to mapping.

**If NO accounts are found**, guide login based on repo orgs:

- **Same org** (likely 1 account):
  > "You're not signed in to GitHub CLI. Let's sign in with the account that has
  > access to `<org>/*` repos."
  Guide: `gh auth login --hostname github.com --git-protocol https --web`

- **Multiple orgs** (likely multiple accounts):
  > "Your repos span multiple GitHub organizations (`<org1>`, `<org2>`), so you'll
  > likely need separate accounts. Let's start with `<org1>/*`."
  Guide: `gh auth login --hostname github.com --git-protocol https --web`
  After first login, ask:
  ```
  askQuestion({
    question: "Do you need a different account for <org2>/* repos?",
    options: [
      { label: "Yes, sign in with another account", description: "I use a separate account for <org2>" },
      { label: "No, same account", description: "My account has access to all orgs" }
    ]
  })
  ```
  If yes, guide another `gh auth login`.

After all logins, re-run `gh auth status` and present discovered accounts:
```markdown
## GitHub Accounts Found
1. `johndoe` (github.com)
2. `johndoe_microsoft` (github.com)
```

#### Map GitHub Accounts to Repositories

Tell the user:
> "Each GitHub repository needs to be assigned to a specific account. The orchestrator
> uses `gh auth switch --user <username>` before running commands against that repo."

**If only ONE GitHub account** — auto-assign to all GitHub repos. Confirm:
```
get_confirmation({
  message: "Only one GitHub account found (<username>). Use it for all GitHub repos?",
  confirmLabel: "Yes",
  denyLabel: "No, I need to add another account"
})
```
If denied, guide `gh auth login` for an additional account, then do per-repo mapping.

**If MULTIPLE GitHub accounts** — ask per-repo.
**IMPORTANT**: Include the repo slug clearly in each question so the user knows which repo:

```
askQuestion({
  questions: [
    {
      header: "Account for: microsoft/VerifiableCredential-SDK-Android",
      question: "Which GitHub account should be used for microsoft/VerifiableCredential-SDK-Android?",
      options: [
        { label: "johndoe", description: "github.com" },
        { label: "johndoe_microsoft", description: "github.com (EMU)" }
      ]
    },
    {
      header: "Account for: microsoft/entra-verifiedid-wallet-library-android",
      question: "Which GitHub account should be used for microsoft/entra-verifiedid-wallet-library-android?",
      options: [
        { label: "johndoe", description: "github.com" },
        { label: "johndoe_microsoft", description: "github.com (EMU)" }
      ]
    }
  ]
})
```

Save to **`developer-local.json` ONLY** (per-developer, gitignored):
```json
{
  "github_accounts": {
    "org/common-repo": "johndoe",
    "org/service-repo": "johndoe_microsoft",
    "other-org/client-repo": "johndoe-alt"
  }
}
```

**⚠️ NEVER put GitHub usernames in `orchestrator-config.json`** — that file is committed
and shared with the team. Usernames belong only in `developer-local.json`. The shared
config should only contain:
```json
"github": {
  "configFile": ".github/developer-local.json"
}
```

Tell the user to add `.github/developer-local.json` to `.gitignore` if not already there.

#### Azure DevOps Account Discovery (skip if no ADO config)

**Skip if** there are no ADO-hosted repos AND no ADO work item configuration needed.

Tell the user:
> "Checking Azure CLI authentication. This enables the ADO MCP Server to create work items
> and query iterations."

```powershell
az --version
az account show --only-show-errors -o none 2>&1
```

If `az` is installed:
1. Check `azure-devops` extension:
   ```powershell
   az extension list -o json | ConvertFrom-Json | Where-Object { $_.name -eq "azure-devops" }
   ```
   If missing: `az extension add --name azure-devops`

2. Check authentication:
   ```powershell
   az account show -o json
   ```
   If not authenticated: `az login`

3. Set defaults (ADO org/project parsed from repo URLs in Step 2):
   ```powershell
   az devops configure --defaults organization=https://dev.azure.com/<org> project=<project>
   ```

If `az` is not installed:
> "Azure CLI is optional but recommended for live PBI status updates.
> Install: `winget install Microsoft.AzureCLI` (Windows) or `brew install azure-cli` (macOS)"

---

### Step 7: Azure DevOps Work Item Configuration

Tell the user:
> "The orchestrator creates work items (PBIs/User Stories) in Azure DevOps to track
> implementation progress. This step configures which ADO project to use for **work items**
> (your backlog/board). This may be different from the ADO project that hosts your repos."

**Auto-detect from repo URLs**: If any ADO-hosted repos exist, parse the org and project
from their URL as a starting suggestion.

**Important**: The ADO project for **work items** (boards, sprints) may differ from the
project that hosts the **repo**. Always let the user override.

**⚠️ URL Normalization**: If the user provides a full URL (e.g.,
`https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/123`), extract only
the **org name** and **project name** — never store the full URL in the config. Colons in
URLs cause ADO API errors. Store only: `"org": "IdentityDivision"`, `"project": "Engineering"`.

```
askQuestion({
  questions: [
    {
      header: "ADO Organization",
      question: "Azure DevOps organization for work items:",
      options: [
        { label: "<detected-org>", description: "Detected from your ADO repo URL", recommended: true }
      ],
      allowFreeformInput: true
    },
    {
      header: "ADO Project (for work items / board)",
      question: "Which ADO project holds your backlog and sprints? (This may differ from your repo project)",
      options: [
        { label: "<detected-project>", description: "Detected from repo URL — confirm this is where your PBIs live", recommended: true }
      ],
      allowFreeformInput: true
    },
    {
      header: "Work Item Type",
      question: "Default work item type:",
      options: [
        { label: "Product Backlog Item", recommended: true },
        { label: "User Story" },
        { label: "Task" }
      ],
      allowFreeformInput: true
    }
  ]
})
```

---

### Step 8: Design Docs

Tell the user:
> "Before coding, the orchestrator writes a design spec for team review. I need to know
> where to save these specs. If your team has a design doc template, I'll follow it —
> otherwise I'll use a built-in template covering problem, solution options, and trade-offs."

```
askQuestion({
  questions: [
    {
      header: "Design Docs Path",
      question: "Where should design specs be saved?",
      options: [
        { label: "docs/designs/", description: "Standard docs folder" },
        { label: "design-docs/", description: "Dedicated design docs folder" }
      ],
      allowFreeformInput: true
    },
    {
      header: "Design Template",
      question: "Do you have a design spec template?",
      options: [
        { label: "No template", description: "Use the built-in template", recommended: true },
        { label: "Custom template", description: "I'll provide a path" }
      ],
      allowFreeformInput: true
    }
  ]
})
```

---

### Step 9: Generate Codebase Context

Tell the user:
> "I can do a deep scan of your codebase to generate a context file that helps the AI
> understand your architecture, key classes, and patterns. This significantly improves
> research and design quality for every future feature."

Ask whether to proceed:
```
get_confirmation({
  message: "Generate .github/codebase-context.md? This takes a few minutes for large repos but significantly improves AI research quality.",
  confirmLabel: "Yes, scan now",
  denyLabel: "Skip — I'll add it later"
})
```

**If skipped**: Create a minimal placeholder `.github/codebase-context.md`:
```markdown
# Codebase Context

<!-- This file helps the AI understand your codebase. -->
<!-- Run /feature-orchestrator-plugin:setup again and choose "scan" to auto-generate, -->
<!-- or fill in manually. -->

## Architecture
TODO: Describe your high-level architecture.

## Modules
TODO: Describe your key modules and their responsibilities.

## Key Classes & Patterns
TODO: List important classes, interfaces, and patterns.
```

**If confirmed**: Perform a deep automated scan.

#### Scan Strategy

Use a combination of broad search (Explore subagent if available, or grep/file search)
and deep analysis (main model) to discover:

**1. Architecture Pattern**
- Check directory structures for common patterns:
  - `controllers/`, `services/`, `repositories/` → Layered architecture
  - `commands/`, `handlers/`, `queries/` → CQRS
  - `features/` or `modules/` with self-contained sub-dirs → Feature-based
  - Multiple repos with IPC/API boundaries → Distributed / multi-service
- Check for key framework indicators:
  - `@SpringBootApplication`, `@RestController` → Spring Boot
  - `Activity`, `Fragment`, `ViewModel` → Android
  - `express()`, `app.listen` → Node.js/Express
  - `func main()`, `http.ListenAndServe` → Go
- Read `README.md` files in each module for architecture descriptions

**2. Module Deep-dive** (for each module discovered in Step 3)
- Read the module's README if it exists
- Find the main entry point class/file
- List the key public interfaces/classes (look for `public class`, `export`, `interface`)
- Identify the module's dependencies on other modules (import statements, build.gradle dependencies)

**3. Key Classes by Domain**
- Find entry points: `main()`, `Application` classes, exported services
- Find core abstractions: interfaces with multiple implementations
- Find data models: classes in `model/`, `dto/`, `entity/` directories
- Find configuration: files in `config/`, `configuration/` directories
- Find test patterns: test base classes, test utilities, mock factories

**4. Common Patterns & Conventions**
- Error handling: search for custom exception classes, error handling middleware
- Logging: which logger (custom Logger class, SLF4J, Log4j, android.util.Log)
- Feature flags: search for flag/flight/experiment patterns
- Configuration: how config is loaded (env vars, config files, DI)
- Testing: framework (JUnit, pytest, Jest), naming conventions, mock patterns

**5. Dependency Graph**
- For each module, identify which other modules it depends on
- Build a simplified dependency graph

#### Output Format

Generate `.github/codebase-context.md` with this structure:

```markdown
# Codebase Context

> Auto-generated by Feature Orchestrator setup on <date>.
> This file helps the AI understand your codebase for better research and design.
> Feel free to edit and enrich — the more detail, the better the AI performs.

## Architecture

[Architecture pattern description]

```
[ASCII diagram of component flow, e.g.:]
Client App → SDK (msal) → IPC Layer (common) → Service (broker) → Backend (eSTS)
```

## Modules

### <module-name>
- **Purpose**: [from README or inferred]
- **Path**: `<path>/`
- **Key entry points**: `ClassName`, `ClassName2`
- **Depends on**: module-a, module-b
- **Test location**: `<path>/src/test/`

[Repeat for each module]

## Key Classes & Interfaces

### Entry Points
- `ClassName` in `module` — [brief purpose]

### Core Abstractions
- `InterfaceName` in `module` — [brief purpose, N implementations found]

### Data Models
- `ModelClass` in `module` — [brief purpose]

## Patterns & Conventions

### Error Handling
[How errors are handled in this codebase]

### Logging
[Which logger, any custom wrapper]

### Feature Flags
[How features are gated]

### Testing
[Framework, patterns, where tests live]

## Search Tips

When researching this codebase:
- To find operations: `file_search(**/*Operation*.kt)`
- To find controllers: `grep_search("class.*Controller")`
- [More project-specific search guidance]
```

Tell the user when done:
> "Codebase context generated at `.github/codebase-context.md`. You can review and
> enrich it over time — the more detail, the better the AI performs during research
> and design."

---

### Step 10: Finalize — Write Config & Install State CLI

Tell the user:
> "Great — I have everything I need. Let me write the configuration and set up the
> state tracking."

#### Write Config

Save `.github/orchestrator-config.json`:

```json
{
  "project": {
    "name": "<name>",
    "description": "<description>"
  },
  "repositories": {
    "<repo-key>": {
      "slug": "<org/repo>",
      "host": "github",
      "baseBranch": "dev"
    }
  },
  "modules": {
    "<module-name>": {
      "repo": "<repo-key>",
      "path": "<path-within-repo>/",
      "purpose": "<brief description>"
    }
  },
  "github": {
    "configFile": ".github/developer-local.json"
  },
  "ado": {
    "org": "<org>",
    "project": "<project>",
    "workItemType": "Product Backlog Item",
    "iterationDepth": 6
  },
  "design": {
    "docsPath": "<path>",
    "templatePath": null,
    "folderPattern": "[{platform}] {featureName}",
    "reviewRepo": null
  }
}
```

> "This file should be committed to your repo so your whole team shares the same settings."

#### Install State CLI

Tell the user:
> "Installing the state tracking script to `~/.feature-orchestrator/`. This keeps track
> of which features are in progress, their current pipeline stage, and associated work items
> and PRs. It's shared across all your projects."

Copy `state-utils.js` to the fixed global location `~/.feature-orchestrator/`:
```powershell
$stateDir = Join-Path $HOME ".feature-orchestrator"
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }

# Find state-utils.js from the plugin installation
$pluginStateUtils = Join-Path (Split-Path (Split-Path $PSScriptRoot)) "hooks" "state-utils.js"
if (Test-Path $pluginStateUtils) {
    Copy-Item $pluginStateUtils (Join-Path $stateDir "state-utils.js") -Force
} else {
    # Fallback: read the content from the plugin's hooks/state-utils.js and write it
    Write-Host "Please manually copy state-utils.js to $stateDir"
}
```

**Important**: If the above doesn't find the file automatically, the agent should:
1. Read the `state-utils.js` content from the plugin's `hooks/state-utils.js`
2. Write it to `~/.feature-orchestrator/state-utils.js`

Verify it works:
```powershell
node (Join-Path $HOME ".feature-orchestrator" "state-utils.js") get
```

#### Configure ADO MCP Server (if ADO is used)

**Skip if** there is no ADO configuration (Step 7 was skipped).

Tell the user:
> "Setting up the ADO MCP Server so Copilot can create and manage work items.
> This writes a `.vscode/mcp.json` file in your workspace."

Check if `.vscode/mcp.json` already exists in the workspace:
- If it exists, read it and check if an `ado` server is already configured.
  If so, verify the org matches — if different, ask whether to update.
- If it doesn't exist, create it.

Write `.vscode/mcp.json` with the org from Step 7 **hardcoded** (no `${input}` prompt):

```json
{
  "servers": {
    "ado": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@azure-devops/mcp",
        "<org-name-from-step-7>",
        "-d",
        "core",
        "work",
        "work-items",
        "repositories",
        "pipelines"
      ]
    }
  }
}
```

**⚠️ IMPORTANT**: The org argument must be a **plain org name** (e.g., `IdentityDivision`),
NOT a URL. If the user provided a URL in Step 7, it was already normalized to just the
org name in the config — use that normalized value.

If `.vscode/mcp.json` already exists with other servers, **merge** the `ado` server entry
into the existing file — do not overwrite other servers.

Tell the user:
> "ADO MCP Server configured in `.vscode/mcp.json`. You may need to restart the MCP server
> (Command Palette → `MCP: Restart Server` → `ado`) or reload VS Code for it to take effect."

---

### Done!

**Always end with this exact summary format** — it gives the user a clear overview of
everything that was configured:

```markdown
## ✅ Feature Orchestrator Configured!

**Config saved**: `.github/orchestrator-config.json`
**State directory**: `~/.feature-orchestrator/`
**Developer config**: `.github/developer-local.json` (add to `.gitignore`)

### Detected Setup

| Component | Status |
|-----------|--------|
| **Repos** | N repos (X ADO, Y GitHub) |
| **Modules** | N modules mapped |
| **ADO** | <org> / <project> (PBI) |
| **Design docs** | <docsPath>/ |
| **Node.js** | vX.Y.Z ✅ |
| **GitHub CLI** | vX.Y.Z ✅ (N accounts: user1, user2) |
| **Azure CLI** | vX.Y.Z ✅ (user@domain) |
| **State CLI** | Working ✅ (N features tracked) |

For any component that is missing or not configured, show ⚠️ or ❌ with guidance:
- Not installed: `❌ Not installed — run: <install command>`
- Not authenticated: `⚠️ Not authenticated — run: <auth command>`
- Not applicable: `— (not needed)` (e.g., GitHub CLI when all repos are ADO)

### Available Commands

| Command | Description |
|---------|-------------|
| `feature-design` | Start a new feature with design |
| `feature-plan` | Decompose design into work items |
| `feature-backlog` | Create work items in ADO |
| `feature-dispatch` | Send to Copilot coding agent |
| `feature-status` | Check PR status |
| `feature-continue` | Resume a feature |
| `feature-pr-iterate` | Review and iterate on PRs |

### Quick Start

Describe a feature to get started:
> "I want to add retry logic with exponential backoff to the API client"

Or use `/feature-orchestrator-plugin:feature-design` followed by your feature description.
```
