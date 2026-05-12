<#
.SYNOPSIS
    Build and deploy the android-dri-indexer as a Container App Job (cron).

.DESCRIPTION
    1. Builds the Docker image in ACR.
    2. Creates a Container App Job with a 12-hour cron schedule.
    3. Uses the existing managed identity (android-dri-mcp-identity).

.NOTES
    Prerequisites:
    - az CLI logged in with appropriate permissions
    - The managed identity needs:
      * "Search Index Data Contributor" on the Azure Search resource
      * "Cognitive Services OpenAI User" on the Azure OpenAI resource
      * Kusto cluster viewer access (add as AllDatabasesViewer)
      * Azure DevOps token exchange (MSI must be in the ADO org)
#>

param(
    [string]$Tag = "v1",
    [string]$ResourceGroup = "rg-android-dri-mcp",
    [string]$Registry = "androiddrimcp",
    [string]$Environment = "android-dri-mcp-env",
    [string]$JobName = "android-dri-indexer",
    [string]$IdentityResourceId = "/subscriptions/cde31ea7-d66a-4743-af52-1d2c0940779c/resourceGroups/rg-android-dri-mcp/providers/Microsoft.ManagedIdentity/userAssignedIdentities/android-dri-mcp-identity",
    [string]$IdentityClientId = "ef52deba-2e45-4dbf-a6d8-7251242354b4"
)

$ErrorActionPreference = "Stop"
$ImageName = "$Registry.azurecr.io/${JobName}:$Tag"

# ── Step 1: Build image in ACR ───────────────────────────────────────────
Write-Host "`n=== Building $ImageName in ACR ===" -ForegroundColor Cyan
Push-Location $PSScriptRoot
try {
    az acr build `
        --registry $Registry `
        --image "${JobName}:$Tag" `
        --file Dockerfile `
        . `
        --no-logs
} finally {
    Pop-Location
}
Write-Host "Build complete." -ForegroundColor Green

# ── Step 2: Create or update Container App Job ──────────────────────────
Write-Host "`n=== Creating Container App Job: $JobName ===" -ForegroundColor Cyan

# Check if job already exists
$exists = az containerapp job show `
    --name $JobName `
    --resource-group $ResourceGroup `
    2>$null

if ($exists) {
    Write-Host "Job exists — updating image to $ImageName"
    az containerapp job update `
        --name $JobName `
        --resource-group $ResourceGroup `
        --image $ImageName
} else {
    Write-Host "Creating new job…"
    az containerapp job create `
        --name $JobName `
        --resource-group $ResourceGroup `
        --environment $Environment `
        --trigger-type "Schedule" `
        --cron-expression "0 */12 * * *" `
        --image $ImageName `
        --registry-server "$Registry.azurecr.io" `
        --registry-identity $IdentityResourceId `
        --cpu 1.0 `
        --memory 2Gi `
        --replica-timeout 3600 `
        --replica-retry-limit 1 `
        --mi-user-assigned $IdentityResourceId `
        --env-vars `
            "AZURE_CLIENT_ID=$IdentityClientId" `
            "AZURE_SEARCH_ENDPOINT=https://msalandroiddricopilotsearch.search.windows.net" `
            "AZURE_OPENAI_ENDPOINT=https://msal-android-dri-copilot-oai.openai.azure.com/" `
            "ICM_LOOKBACK_HOURS=24"
}

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host "Job '$JobName' deployed with cron schedule '0 */12 * * *' (every 12 hours)."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  # Trigger a manual run:"
Write-Host "  az containerapp job start --name $JobName --resource-group $ResourceGroup"
Write-Host ""
Write-Host "  # View execution history:"
Write-Host "  az containerapp job execution list --name $JobName --resource-group $ResourceGroup -o table"
Write-Host ""
Write-Host "  # View logs from latest execution:"
Write-Host "  az containerapp job logs show --name $JobName --resource-group $ResourceGroup"
