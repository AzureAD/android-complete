<#
.SYNOPSIS
  Build, push, and deploy the android_dri_indexer as an Azure Container App Job.

.DESCRIPTION
  Reuses the existing Container App Environment (android-dri-mcp-env) and
  user-assigned managed identity (msal-android-dri-copilot-identity) from the MCP server
  deployment. The job runs on a cron schedule (daily at 06:00 UTC by default).

.PARAMETER Tag
  Docker image tag. Defaults to 'latest'.

.PARAMETER Schedule
  Cron expression for the job schedule. Default: daily 06:00 UTC.

.PARAMETER RunNow
  If set, triggers an immediate execution after create/update.
#>
param(
    [string]$Tag = "latest",
    [string]$Schedule = "0 6 * * *",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

# ── Configuration ───────────────────────────────────────────────────────────
$ACR = "androiddrimcp.azurecr.io"
$IMAGE = "$ACR/android-dri-icm-indexer:$Tag"
$RG = "rg-android-dri-mcp"
$IDENTITY_RG = "MsalAndroidDriCopilot1"
$ENV_NAME = "android-dri-mcp-env"
$IDENTITY_NAME = "msal-android-dri-copilot-identity"
$JOB_NAME = "android-dri-icm-indexer"
$LOCATION = "eastus"

# ── Resolve identity resource ID and client ID ──────────────────────────────
Write-Host "Resolving managed identity..." -ForegroundColor Cyan
$identityJson = az identity show --name $IDENTITY_NAME --resource-group $IDENTITY_RG | ConvertFrom-Json
$IDENTITY_ID = $identityJson.id
$CLIENT_ID = $identityJson.clientId
Write-Host "  Identity: $IDENTITY_ID"
Write-Host "  Client ID: $CLIENT_ID"

# ── Build and push image ────────────────────────────────────────────────────
# The indexer Dockerfile extends the MCP server image (FROM androiddrimcp.azurecr.io/android-dri-mcp:latest)
# so both share the same base — one S360 item for OS-level CVEs.
Write-Host "`nBuilding image: $IMAGE" -ForegroundColor Cyan
Push-Location (Split-Path $PSScriptRoot -Parent)  # android_dri_indexer/
az acr build --registry androiddrimcp --image "android-dri-icm-indexer:$Tag" --file Dockerfile .
Pop-Location

# ── Create or update the Container App Job ──────────────────────────────────
Write-Host "`nCreating/updating Container App Job: $JOB_NAME" -ForegroundColor Cyan

# Check if job already exists
$exists = az containerapp job show --name $JOB_NAME --resource-group $RG 2>$null
if ($exists) {
    Write-Host "  Job exists — updating image..."
    az containerapp job update `
        --name $JOB_NAME `
        --resource-group $RG `
        --image $IMAGE `
        --cron-expression $Schedule
} else {
    Write-Host "  Creating new job..."
    az containerapp job create `
        --name $JOB_NAME `
        --resource-group $RG `
        --environment $ENV_NAME `
        --image $IMAGE `
        --registry-server $ACR `
        --registry-identity $IDENTITY_ID `
        --mi-user-assigned $IDENTITY_ID `
        --trigger-type Schedule `
        --cron-expression $Schedule `
        --replica-timeout 7200 `
        --replica-retry-limit 1 `
        --cpu 1.0 `
        --memory 2Gi `
        --env-vars "AZURE_CLIENT_ID=$CLIENT_ID" `
            "AZURE_SEARCH_ENDPOINT=https://msalandroiddricopilotsearch.search.windows.net" `
            "AZURE_OPENAI_ENDPOINT=https://msal-android-dri-copilot-oai.openai.azure.com/"
}

Write-Host "`nDone! Job '$JOB_NAME' is configured." -ForegroundColor Green
Write-Host "Schedule: $Schedule (cron)"
Write-Host "Image: $IMAGE"

# ── Optionally trigger immediately ──────────────────────────────────────────
if ($RunNow) {
    Write-Host "`nTriggering immediate execution..." -ForegroundColor Yellow
    az containerapp job start --name $JOB_NAME --resource-group $RG
    Write-Host "Job started. Check status in Azure Portal → Container App Jobs → $JOB_NAME"
}
