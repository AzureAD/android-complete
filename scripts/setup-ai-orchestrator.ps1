#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Setup script for the AI-Driven Feature Orchestrator.
    Ensures all prerequisites are installed, authenticated, and configured.

.DESCRIPTION
    Run this once to set up your environment for AI-driven feature development.
    It checks/installs tools, authenticates GitHub accounts, clones design-docs,
    and installs the Feature Orchestrator VS Code extension.

.EXAMPLE
    .\scripts\setup-ai-orchestrator.ps1
#>

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $repoRoot ".github"))) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path (Join-Path $repoRoot ".github"))) {
        $repoRoot = $PWD.Path
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI Feature Orchestrator Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repo root: $repoRoot"
Write-Host ""

$issues = @()
$warnings = @()

# ============================================================
# 0. Check VS Code & Copilot
# ============================================================
Write-Host "[0/7] Checking VS Code & GitHub Copilot..." -ForegroundColor Yellow

# Check VS Code
try {
    $codeVersion = (code --version 2>&1 | Select-Object -First 1)
    $versionParts = $codeVersion -split "\."
    $major = [int]$versionParts[0]
    $minor = [int]$versionParts[1]
    if ($major -gt 1 -or ($major -eq 1 -and $minor -ge 109)) {
        Write-Host "  OK: VS Code $codeVersion" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: VS Code $codeVersion is too old. Requires >= 1.109 for agents, skills, prompt files, and askQuestion." -ForegroundColor Yellow
        Write-Host "  Update: Help > Check for Updates, or download from https://code.visualstudio.com" -ForegroundColor Yellow
        Write-Host "  (The rest of setup will continue -- update VS Code afterward and you're good)" -ForegroundColor DarkGray
        $warnings += "VS Code version $codeVersion is below 1.109. Update via Help > Check for Updates."
    }
} catch {
    Write-Host "  MISSING: VS Code ('code' command not found)." -ForegroundColor Red
    Write-Host "  Install from https://code.visualstudio.com and ensure 'code' is in PATH." -ForegroundColor Red
    exit 1
}

# Check GitHub Copilot extension
try {
    $copilotExtensions = code --list-extensions 2>&1 | Select-String -Pattern "github\.copilot"
    if ($copilotExtensions) {
        Write-Host "  OK: GitHub Copilot installed" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: GitHub Copilot extension not found." -ForegroundColor Yellow
        Write-Host "  Install from VS Code: Extensions > search 'GitHub Copilot' > Install" -ForegroundColor Yellow
        Write-Host "  (Requires a GitHub Copilot license. The rest of setup will continue)" -ForegroundColor DarkGray
        $warnings += "GitHub Copilot extension is required for agents and prompt files."
    }
} catch {
    # Couldn't check -- skip
}

# ============================================================
# 1. Check Node.js
# ============================================================
Write-Host "[1/7] Checking Node.js..." -ForegroundColor Yellow
try {
    $nodeVersion = (node --version 2>&1)
    Write-Host "  OK: Node.js $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "  MISSING: Node.js (required for hooks, state management, and extension build)" -ForegroundColor Red
    $install = Read-Host "  Install Node.js now? (Y/n)"
    if ($install -ne "n") {
        Write-Host "  Installing via winget..." -ForegroundColor Cyan
        winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
        Write-Host "  Installed. Please restart your terminal and re-run this script." -ForegroundColor Yellow
        Write-Host "  (Node.js won't be available until the terminal is restarted)" -ForegroundColor Yellow
        exit 0
    } else {
        Write-Host "  Node.js is required. Install it and re-run this script." -ForegroundColor Red
        exit 1
    }
}

# ============================================================
# 2. Check & install GitHub CLI
# ============================================================
Write-Host "[2/7] Checking GitHub CLI (gh)..." -ForegroundColor Yellow
try {
    $ghVersion = (gh --version 2>&1 | Select-Object -First 1)
    Write-Host "  OK: $ghVersion" -ForegroundColor Green
} catch {
    Write-Host "  MISSING: GitHub CLI" -ForegroundColor Red
    $install = Read-Host "  Install GitHub CLI now? (Y/n)"
    if ($install -ne "n") {
        Write-Host "  Installing via winget..." -ForegroundColor Cyan
        winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
        Write-Host "  Installed. You may need to restart your terminal." -ForegroundColor Green
    } else {
        $issues += "GitHub CLI (gh) is required for dispatching PBIs and checking PR status."
    }
}

# ============================================================
# 3. Authenticate GitHub accounts
# ============================================================
Write-Host "[3/7] Checking GitHub authentication..." -ForegroundColor Yellow

$devLocalPath = Join-Path $repoRoot ".github" "developer-local.json"
$ghAccounts = @{}

# Check if developer-local.json already exists
if (Test-Path $devLocalPath) {
    try {
        $config = Get-Content $devLocalPath -Raw | ConvertFrom-Json
        $ghAccounts = @{
            "AzureAD" = $config.github_accounts.AzureAD
            "identity-authnz-teams" = $config.github_accounts."identity-authnz-teams"
        }
        Write-Host "  OK: developer-local.json exists" -ForegroundColor Green
        Write-Host "    AzureAD account: $($ghAccounts['AzureAD'])" -ForegroundColor DarkGray
        Write-Host "    EMU account: $($ghAccounts['identity-authnz-teams'])" -ForegroundColor DarkGray
    } catch {
        Write-Host "  WARNING: developer-local.json exists but couldn't be parsed" -ForegroundColor Yellow
    }
}

if (-not $ghAccounts["AzureAD"] -or -not $ghAccounts["identity-authnz-teams"]) {
    Write-Host "  GitHub accounts not configured. Let's set them up." -ForegroundColor Yellow
    Write-Host ""

    # Discover logged-in accounts from gh auth status
    $loggedIn = @()
    $discoveredPublic = ""
    $discoveredEmu = ""
    try {
        $ghStatus = gh auth status 2>&1
        foreach ($line in $ghStatus) {
            $match = [regex]::Match($line, "account\s+(\S+)")
            if ($match.Success) { $loggedIn += $match.Groups[1].Value }
        }
        if ($loggedIn.Count -gt 0) {
            Write-Host "  Found logged-in GitHub accounts:" -ForegroundColor Green
            foreach ($acct in $loggedIn) {
                if ($acct -match "_") {
                    Write-Host "    EMU: $acct" -ForegroundColor DarkGray
                    if (-not $discoveredEmu) { $discoveredEmu = $acct }
                } else {
                    Write-Host "    Public: $acct" -ForegroundColor DarkGray
                    if (-not $discoveredPublic) { $discoveredPublic = $acct }
                }
            }
        }
    } catch {
        # gh not authenticated at all
    }

    # Public account (AzureAD repos)
    if (-not $ghAccounts["AzureAD"]) {
        Write-Host ""
        Write-Host "  You need a PUBLIC GitHub account for AzureAD/* repos (common, msal, adal)." -ForegroundColor Cyan
        if ($discoveredPublic) {
            $confirm = Read-Host "  Use discovered account '$discoveredPublic'? (Y/n)"
            if ($confirm -ne "n") {
                $publicUser = $discoveredPublic
            } else {
                $publicUser = Read-Host "  Enter your public GitHub username"
            }
        } else {
            $publicUser = Read-Host "  Enter your public GitHub username (e.g., johndoe)"
        }
        if ($publicUser) {
            $ghAccounts["AzureAD"] = $publicUser
            $isLoggedIn = $loggedIn -contains $publicUser
            if (-not $isLoggedIn) {
                Write-Host "  Authenticating $publicUser with GitHub..." -ForegroundColor Cyan
                gh auth login --hostname github.com --git-protocol https --web
            }
        }
    }

    # EMU account (identity-authnz-teams repos)
    if (-not $ghAccounts["identity-authnz-teams"]) {
        Write-Host ""
        Write-Host "  You need an EMU GitHub account for identity-authnz-teams/* repos (broker)." -ForegroundColor Cyan
        if ($discoveredEmu) {
            $confirm = Read-Host "  Use discovered account '$discoveredEmu'? (Y/n)"
            if ($confirm -ne "n") {
                $emuUser = $discoveredEmu
            } else {
                $emuUser = Read-Host "  Enter your EMU GitHub username"
            }
        } else {
            $emuUser = Read-Host "  Enter your EMU GitHub username (e.g., johndoe_microsoft)"
        }
        if ($emuUser) {
            $ghAccounts["identity-authnz-teams"] = $emuUser
            $isLoggedIn = $loggedIn -contains $emuUser
            if (-not $isLoggedIn) {
                Write-Host "  Authenticating $emuUser with GitHub..." -ForegroundColor Cyan
                gh auth login --hostname github.com --git-protocol https --web
            }
        }
    }

    # Save developer-local.json
    if ($ghAccounts["AzureAD"] -and $ghAccounts["identity-authnz-teams"]) {
        $config = @{
            github_accounts = @{
                AzureAD = $ghAccounts["AzureAD"]
                "identity-authnz-teams" = $ghAccounts["identity-authnz-teams"]
            }
        }
        $configDir = Join-Path $repoRoot ".github"
        if (-not (Test-Path $configDir)) { New-Item -ItemType Directory -Path $configDir -Force | Out-Null }
        $config | ConvertTo-Json -Depth 3 | Set-Content $devLocalPath -Encoding UTF8
        Write-Host "  Saved to .github/developer-local.json" -ForegroundColor Green
    } else {
        $warnings += "GitHub accounts not fully configured. Dispatch and PR monitoring may not work."
    }
}

# ============================================================
# 4. Check Azure CLI + DevOps extension
# ============================================================
Write-Host "[4/7] Checking Azure CLI (az)..." -ForegroundColor Yellow
try {
    $azVersion = (az version -o tsv 2>&1 | Select-Object -First 1)
    Write-Host "  OK: Azure CLI installed" -ForegroundColor Green

    # Check azure-devops extension
    $extensions = az extension list -o json 2>&1 | ConvertFrom-Json
    $hasDevOps = $extensions | Where-Object { $_.name -eq "azure-devops" }
    if ($hasDevOps) {
        Write-Host "  OK: azure-devops extension installed" -ForegroundColor Green
    } else {
        Write-Host "  Installing azure-devops extension..." -ForegroundColor Cyan
        az extension add --name azure-devops 2>&1 | Out-Null
        Write-Host "  Installed." -ForegroundColor Green
    }

    # Check if logged in
    try {
        az account show --only-show-errors -o none 2>&1 | Out-Null
        Write-Host "  OK: Authenticated with Azure" -ForegroundColor Green
    } catch {
        Write-Host "  Not authenticated. Running az login..." -ForegroundColor Yellow
        az login --output none
    }
} catch {
    Write-Host "  OPTIONAL: Azure CLI not installed" -ForegroundColor Yellow
    Write-Host "  (Needed for live PBI status refresh. Install: winget install Microsoft.AzureCLI)" -ForegroundColor DarkGray
    $warnings += "Azure CLI not installed. PBI status refresh in the dashboard will be unavailable."
}

# ============================================================
# 5. Clone design-docs if missing
# ============================================================
Write-Host "[5/7] Checking design-docs..." -ForegroundColor Yellow
$designDocsPath = Join-Path $repoRoot "design-docs"
if (Test-Path $designDocsPath) {
    $isGitRepo = Test-Path (Join-Path $designDocsPath ".git")
    if ($isGitRepo) {
        Write-Host "  OK: design-docs/ exists and is a git repo" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: design-docs/ exists but is not a git repo. Design PR creation may not work." -ForegroundColor Yellow
        Write-Host "  Consider deleting it and re-running this script to clone properly." -ForegroundColor Yellow
        $warnings += "design-docs/ is not a git repo. Delete it and re-run setup to clone from ADO."
    }
} else {
    Write-Host "  design-docs/ not found. Cloning from ADO..." -ForegroundColor Yellow
    try {
        git clone -b dev "https://dev.azure.com/IdentityDivision/DevEx/_git/AuthLibrariesApiReview" $designDocsPath 2>&1 | Out-Null
        Write-Host "  Cloned successfully." -ForegroundColor Green
    } catch {
        Write-Host "  Failed to clone. You may need to run 'git droidSetup' or clone manually." -ForegroundColor Red
        $warnings += "design-docs/ not available. Design authoring will be limited."
    }
}

# ============================================================
# 6. Build & install the extension
# ============================================================
Write-Host "[6/7] Building Feature Orchestrator extension..." -ForegroundColor Yellow
$extDir = Join-Path $repoRoot "extensions" "feature-orchestrator"

if (Test-Path (Join-Path $extDir "package.json")) {
    Push-Location $extDir

    # Install npm dependencies if needed
    if (-not (Test-Path "node_modules")) {
        Write-Host "  Installing npm dependencies..." -ForegroundColor Cyan
        npm install 2>&1 | Out-Null
    }

    # Build
    Write-Host "  Compiling TypeScript..." -ForegroundColor Cyan
    npm run compile 2>&1 | Out-Null

    # Package
    Write-Host "  Packaging VSIX..." -ForegroundColor Cyan
    Write-Output "y" | npx @vscode/vsce package --no-dependencies --allow-missing-repository -o feature-orchestrator-latest.vsix 2>&1 | Out-Null

    if (Test-Path "feature-orchestrator-latest.vsix") {
        Write-Host "  Installing extension..." -ForegroundColor Cyan
        code --install-extension feature-orchestrator-latest.vsix --force 2>&1 | Out-Null
        Write-Host "  OK: Extension installed" -ForegroundColor Green
    } else {
        $issues += "Failed to package the extension. Try running manually in extensions/feature-orchestrator/"
        Write-Host "  FAILED: Could not package extension" -ForegroundColor Red
    }

    Pop-Location
} else {
    $issues += "Extension source not found at extensions/feature-orchestrator/"
    Write-Host "  MISSING: Extension source not found" -ForegroundColor Red
}

# ============================================================
# 7. Create state directory
# ============================================================
Write-Host "[7/7] Setting up state directory..." -ForegroundColor Yellow
$stateDir = Join-Path $env:USERPROFILE ".android-auth-orchestrator"
if (-not (Test-Path $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}
Write-Host "  OK: $stateDir" -ForegroundColor Green

# ============================================================
# Summary
# ============================================================
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($issues.Count -gt 0) {
    Write-Host "ISSUES (must fix):" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host "  - $issue" -ForegroundColor Red
    }
    Write-Host ""
}

if ($warnings.Count -gt 0) {
    Write-Host "WARNINGS (optional):" -ForegroundColor Yellow
    foreach ($warning in $warnings) {
        Write-Host "  - $warning" -ForegroundColor Yellow
    }
    Write-Host ""
}

if ($issues.Count -eq 0) {
    Write-Host "You're ready to go!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Quick start:" -ForegroundColor Cyan
    Write-Host "  1. Reload VS Code window (Ctrl+Shift+P > 'Reload Window')" -ForegroundColor White
    Write-Host "  2. Open the Feature Orchestrator sidebar (rocket icon)" -ForegroundColor White
    Write-Host "  3. Click '+' or type '/feature-design <description>' in chat" -ForegroundColor White
    Write-Host ""
    Write-Host "Available prompt commands:" -ForegroundColor Cyan
    Write-Host "  /feature-design     Start a new feature" -ForegroundColor White
    Write-Host "  /feature-plan       Decompose design into PBIs" -ForegroundColor White
    Write-Host "  /feature-backlog    Create PBIs in Azure DevOps" -ForegroundColor White
    Write-Host "  /feature-dispatch   Dispatch to Copilot coding agent" -ForegroundColor White
    Write-Host "  /feature-status     Check PR status" -ForegroundColor White
    Write-Host "  /feature-continue   Resume from current step" -ForegroundColor White
    Write-Host ""
}
