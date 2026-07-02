<#
.SYNOPSIS
  Deploy the Android DRI MCP Server + Indexer infrastructure for a new team.

.DESCRIPTION
  This script provisions all automatable Azure resources for a team adopting
  the DRI MCP search pattern. It creates:
    - Resource Group
    - Container Apps Environment + Log Analytics
    - Azure Container Registry
    - User-Assigned Managed Identity
    - RBAC role assignments
    - Container App (MCP server)
    - Container App Jobs (ICM + TSG indexers)
    - Azure AI Search indexes + data sources + pull indexers

  Prerequisites (manual, before running this script):
    1. Entra ID App Registration (public client, groupMembershipClaims=SecurityGroup)
    2. Security Group with team members
    3. ICM certificate uploaded to Key Vault
    4. Azure OpenAI resource with text-embedding-3-large + gpt-4o deployments
    5. Azure AI Search service provisioned
    6. Storage Account with a blob container for TSG chunks

  Note: the Managed Identity is created BY this script (Step 3), so identity-dependent
  grants (ADO wiki, Kusto, Key Vault access) are done AFTER it runs — see "Next steps"
  printed at the end.

.PARAMETER ConfigFile
  Path to your filled-in config JSON (based on config_template.json).

.PARAMETER SkipBuild
  Skip Docker image builds (useful if images already exist in ACR).

.PARAMETER DryRun
  Show what would be created without actually creating anything.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigFile,

    [switch]$SkipBuild,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:AZURE_CLI_DISABLE_CONNECTION_VERIFICATION = "1"

# ── Load Config ─────────────────────────────────────────────────────────────
Write-Host "Loading config from $ConfigFile..." -ForegroundColor Cyan
$config = Get-Content $ConfigFile -Raw | ConvertFrom-Json

$SUB = $config.azure.subscription_id
$LOCATION = $config.azure.location
$RG = $config.azure.resource_group
$TEAM = $config.team_name
$ACR_NAME = $config.acr.name
$ACR_SERVER = $config.acr.login_server
$SEARCH_ENDPOINT = $config.azure_search.endpoint
$TSG_INDEX = $config.azure_search.tsg_index_name
$ICM_INDEX = $config.azure_search.icm_index_name
$OPENAI_ENDPOINT = $config.azure_openai.endpoint
$TENANT_ID = $config.auth.tenant_id
$APP_CLIENT_ID = $config.auth.app_registration_client_id
$SG_ID = $config.auth.security_group_object_id
$ALLOWED_APPS = $config.auth.allowed_app_ids
$KV_URI = $config.keyvault.uri

Write-Host "  Team: $TEAM"
Write-Host "  Resource Group: $RG"
Write-Host "  Location: $LOCATION"
Write-Host "  ACR: $ACR_SERVER"
Write-Host "  Search: $SEARCH_ENDPOINT"
Write-Host ""

if ($DryRun) {
    Write-Host "[DRY RUN] Would create the following resources:" -ForegroundColor Yellow
    Write-Host "  - Resource Group: $RG"
    Write-Host "  - Container Apps Environment: $TEAM-mcp-env"
    Write-Host "  - Log Analytics Workspace: $TEAM-mcp-logs"
    Write-Host "  - Container Registry: $ACR_NAME"
    Write-Host "  - Managed Identity: $TEAM-mcp-identity"
    Write-Host "  - RBAC: Search Index Data Reader + Cognitive Services OpenAI User + Storage Blob Data Contributor"
    Write-Host "  - Search Index: $TSG_INDEX"
    Write-Host "  - Search Index: $ICM_INDEX"
    Write-Host "  - ACS Data Source: $TEAM-tsg-blob"
    Write-Host "  - ACS Indexer: $TEAM-tsg-indexer"
    Write-Host "  - Container App: $TEAM-mcp"
    Write-Host "  - Container App Job: $TEAM-icm-indexer"
    Write-Host "  - Container App Job: $TEAM-tsg-indexer"
    Write-Host ""
    Write-Host "[DRY RUN] Complete. Re-run without -DryRun to create resources." -ForegroundColor Yellow
    return
}

# ── Step 1: Resource Group ──────────────────────────────────────────────────
Write-Host "`n[1/9] Creating Resource Group: $RG" -ForegroundColor Cyan
az group create --name $RG --location $LOCATION --subscription $SUB -o none

# ── Step 2: Container Registry ──────────────────────────────────────────────
Write-Host "[2/9] Creating Container Registry: $ACR_NAME" -ForegroundColor Cyan
az acr create --name $ACR_NAME --resource-group $RG --sku Basic --admin-enabled false -o none

# ── Step 3: Managed Identity ────────────────────────────────────────────────
Write-Host "[3/9] Creating Managed Identity: $TEAM-mcp-identity" -ForegroundColor Cyan
$identity = az identity create --name "$TEAM-mcp-identity" --resource-group $RG | ConvertFrom-Json
$IDENTITY_ID = $identity.id
$IDENTITY_PRINCIPAL = $identity.principalId
$IDENTITY_CLIENT = $identity.clientId
Write-Host "  Principal ID: $IDENTITY_PRINCIPAL"
Write-Host "  Client ID: $IDENTITY_CLIENT"

# ── Step 4: RBAC Assignments ────────────────────────────────────────────────
Write-Host "[4/9] Assigning RBAC roles..." -ForegroundColor Cyan

# ACR Pull
Write-Host "  - AcrPull on $ACR_NAME"
$acrId = az acr show --name $ACR_NAME --query id -o tsv
az role assignment create --assignee-object-id $IDENTITY_PRINCIPAL --assignee-principal-type ServicePrincipal --role AcrPull --scope $acrId -o none 2>$null

# Search Index Data Reader
Write-Host "  - Search Index Data Reader"
& "$PSScriptRoot\infra\assign-roles.ps1" -PrincipalId $IDENTITY_PRINCIPAL -SearchEndpoint $SEARCH_ENDPOINT -OpenAIEndpoint $OPENAI_ENDPOINT -SubscriptionId $SUB 2>$null

# Storage Blob Data Contributor (for TSG indexer blob writes)
Write-Host "  - Storage Blob Data Contributor (configure manually if storage is in a different RG)"

# ── Step 5: Container Apps Environment ──────────────────────────────────────
Write-Host "[5/9] Creating Container Apps Environment: $TEAM-mcp-env" -ForegroundColor Cyan
az containerapp env create --name "$TEAM-mcp-env" --resource-group $RG --location $LOCATION -o none

# ── Step 6: Search Indexes + Data Sources + Indexers ────────────────────────
Write-Host "[6/9] Setting up Azure AI Search..." -ForegroundColor Cyan
& "$PSScriptRoot\search\setup-search.ps1" -Config $config

# ── Step 7: Build Docker Images ─────────────────────────────────────────────
if (-not $SkipBuild) {
    Write-Host "[7/9] Building Docker images..." -ForegroundColor Cyan

    $scriptRoot = Split-Path $PSScriptRoot -Parent
    $mcpServerDir = Join-Path $scriptRoot "android_dri_mcp_server"
    $indexerDir = Join-Path $scriptRoot "android_dri_indexer"

    Write-Host "  Building MCP server..."
    az acr build --registry $ACR_NAME --image "$TEAM-mcp:v1" $mcpServerDir --no-logs -o none

    Write-Host "  Building indexer..."
    az acr build --registry $ACR_NAME --image "$TEAM-indexer:v1" --file "$indexerDir/Dockerfile" $indexerDir --no-logs -o none
} else {
    Write-Host "[7/9] Skipping image builds (--SkipBuild)" -ForegroundColor Yellow
}

# ── Step 8: Deploy MCP Server Container App ─────────────────────────────────
Write-Host "[8/9] Creating MCP Server Container App: $TEAM-mcp" -ForegroundColor Cyan

$mcpBody = @{
    location = $LOCATION
    identity = @{
        type = "UserAssigned"
        userAssignedIdentities = @{ $IDENTITY_ID = @{} }
    }
    properties = @{
        environmentId = (az containerapp env show --name "$TEAM-mcp-env" -g $RG --query id -o tsv)
        configuration = @{
            ingress = @{ external = $true; targetPort = 8080; transport = "auto" }
            registries = @(@{ server = $ACR_SERVER; identity = $IDENTITY_ID })
        }
        template = @{
            containers = @(@{
                name = "mcp-server"
                image = "$ACR_SERVER/$TEAM-mcp:v1"
                resources = @{ cpu = 0.5; memory = "1Gi" }
                env = @(
                    @{ name = "MCP_TRANSPORT"; value = "streamable-http" }
                    @{ name = "MCP_HOST"; value = "0.0.0.0" }
                    @{ name = "MCP_PORT"; value = "8080" }
                    @{ name = "AZURE_CLIENT_ID"; value = $IDENTITY_CLIENT }
                    @{ name = "AZURE_SEARCH_ENDPOINT"; value = $SEARCH_ENDPOINT }
                    @{ name = "AZURE_OPENAI_ENDPOINT"; value = $OPENAI_ENDPOINT }
                    @{ name = "AUTH_ENABLED"; value = "true" }
                    @{ name = "AUTH_TENANT_ID"; value = $TENANT_ID }
                    @{ name = "AUTH_CLIENT_ID"; value = $APP_CLIENT_ID }
                    @{ name = "AUTH_ALLOWED_APP_IDS"; value = $ALLOWED_APPS }
                    @{ name = "AUTH_ALLOWED_GROUP_IDS"; value = $SG_ID }
                    @{ name = "OBO_ENABLED"; value = "false" }
                )
            })
            scale = @{ minReplicas = 0; maxReplicas = 1 }
        }
    }
} | ConvertTo-Json -Depth 10 -Compress

$mcpBody | Out-File "$env:TEMP\mcp_app_body.json" -Encoding utf8
az rest --method put --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/containerApps/$TEAM-mcp?api-version=2024-03-01" --body "@$env:TEMP\mcp_app_body.json" --headers "Content-Type=application/json" -o none

$mcpFqdn = az containerapp show --name "$TEAM-mcp" -g $RG --query "properties.configuration.ingress.fqdn" -o tsv
Write-Host "  MCP Server URL: https://$mcpFqdn/mcp" -ForegroundColor Green

# ── Step 9: Deploy Indexer Container App Jobs ───────────────────────────────
Write-Host "[9/9] Creating Indexer Container App Jobs..." -ForegroundColor Cyan

$envId = az containerapp env show --name "$TEAM-mcp-env" -g $RG --query id -o tsv

foreach ($job in @(
    @{ name = "$TEAM-icm-indexer"; cron = "0 6 * * *"; args = @("--config", "android_dri_indexer/configs/config_template.json", "--icm", "--skip-index-setup") },
    @{ name = "$TEAM-tsg-indexer"; cron = "0 */12 * * *"; args = @("--config", "android_dri_indexer/configs/config_template.json", "--tsg", "--skip-index-setup") }
)) {
    Write-Host "  Creating job: $($job.name) (cron: $($job.cron))"

    $jobBody = @{
        location = $LOCATION
        identity = @{ type = "UserAssigned"; userAssignedIdentities = @{ $IDENTITY_ID = @{} } }
        properties = @{
            environmentId = $envId
            configuration = @{
                triggerType = "Schedule"
                replicaTimeout = 7200
                replicaRetryLimit = 1
                scheduleTriggerConfig = @{ cronExpression = $job.cron; parallelism = 1; replicaCompletionCount = 1 }
                registries = @(@{ server = $ACR_SERVER; identity = $IDENTITY_ID })
            }
            template = @{
                containers = @(@{
                    name = "indexer"
                    image = "$ACR_SERVER/$TEAM-indexer:v1"
                    command = @("python3", "-m", "android_dri_indexer")
                    args = $job.args
                    resources = @{ cpu = 1.0; memory = "2Gi" }
                    env = @(
                        @{ name = "AZURE_CLIENT_ID"; value = $IDENTITY_CLIENT }
                        @{ name = "AZURE_SEARCH_ENDPOINT"; value = $SEARCH_ENDPOINT }
                        @{ name = "AZURE_OPENAI_ENDPOINT"; value = $OPENAI_ENDPOINT }
                    )
                })
            }
        }
    } | ConvertTo-Json -Depth 10 -Compress

    $jobBody | Out-File "$env:TEMP\job_body.json" -Encoding utf8
    az rest --method put --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.App/jobs/$($job.name)?api-version=2024-03-01" --body "@$env:TEMP\job_body.json" --headers "Content-Type=application/json" -o none
}

# ── Done ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=" * 60 -ForegroundColor Green
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "=" * 60 -ForegroundColor Green
Write-Host ""
Write-Host "MCP Server URL:" -ForegroundColor White
Write-Host "  https://$mcpFqdn/mcp"
Write-Host ""
Write-Host "Add to .vscode/mcp.json:" -ForegroundColor White
Write-Host @"
  {
    "servers": {
      "$TEAM-dri-search": {
        "type": "http",
        "url": "https://$mcpFqdn/mcp"
      }
    }
  }
"@
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Copy config_template.json to android_dri_indexer/configs/ in the Docker image"
Write-Host "  2. Grant MSI ($IDENTITY_CLIENT) access to ADO wiki repo"
Write-Host "  3. Grant MSI access to Kusto (IcMDataWarehouse)"
Write-Host "  4. Upload ICM certificate to Key Vault"
Write-Host "  5. Run initial ICM backfill: az containerapp job start --name $TEAM-icm-indexer -g $RG"
Write-Host "  6. Run initial TSG index: az containerapp job start --name $TEAM-tsg-indexer -g $RG"
Write-Host "  7. Enable OBO when ready: az containerapp update --name $TEAM-mcp -g $RG --set-env-vars 'OBO_ENABLED=true'"
