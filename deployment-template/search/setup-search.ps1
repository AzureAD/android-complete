<#
.SYNOPSIS
  Create Azure AI Search indexes, data sources, and pull indexers.

.PARAMETER Config
  The parsed config object (from config_template.json).
#>
param(
    [Parameter(Mandatory)] $Config
)

$ErrorActionPreference = "Stop"
$SEARCH = $Config.azure_search.endpoint
$TSG_INDEX = $Config.azure_search.tsg_index_name
$ICM_INDEX = $Config.azure_search.icm_index_name
$TEAM = $Config.team_name
$BLOB_URL = $Config.tsg.blob_container_url
$BLOB_PREFIX = $Config.tsg.blob_prefix

# ── TSG Index Schema ────────────────────────────────────────────────────────
Write-Host "  Creating TSG index: $TSG_INDEX"
$tsgSchema = Get-Content "$PSScriptRoot\tsg_index_schema.json" -Raw
$tsgSchema = $tsgSchema.Replace("{{INDEX_NAME}}", $TSG_INDEX)
$tsgSchema | Out-File "$env:TEMP\tsg_schema.json" -Encoding utf8
az rest --method put --url "$SEARCH/indexes/${TSG_INDEX}?api-version=2024-07-01" --resource "https://search.azure.com" --body "@$env:TEMP\tsg_schema.json" --headers "Content-Type=application/json" -o none 2>$null

# ── ICM Index Schema ────────────────────────────────────────────────────────
Write-Host "  Creating ICM index: $ICM_INDEX"
$icmSchema = Get-Content "$PSScriptRoot\icm_index_schema.json" -Raw
$icmSchema = $icmSchema.Replace("{{INDEX_NAME}}", $ICM_INDEX)
$icmSchema | Out-File "$env:TEMP\icm_schema.json" -Encoding utf8
az rest --method put --url "$SEARCH/indexes/${ICM_INDEX}?api-version=2024-07-01" --resource "https://search.azure.com" --body "@$env:TEMP\icm_schema.json" --headers "Content-Type=application/json" -o none 2>$null

# ── TSG Blob Data Source ────────────────────────────────────────────────────
Write-Host "  Creating data source: $TEAM-tsg-blob"
$containerName = ([Uri]$BLOB_URL).Segments[-1].TrimEnd('/')
$storageAccount = ([Uri]$BLOB_URL).Host.Split('.')[0]
$subId = $Config.azure.subscription_id
$storageRG = (az storage account list --subscription $subId --query "[?name=='$storageAccount'].resourceGroup" -o tsv 2>$null)

$dsBody = @{
    name = "$TEAM-tsg-blob"
    type = "azureblob"
    credentials = @{
        connectionString = "ResourceId=/subscriptions/$subId/resourceGroups/$storageRG/providers/Microsoft.Storage/storageAccounts/$storageAccount;"
    }
    container = @{
        name = $containerName
        query = $BLOB_PREFIX.TrimEnd('/')
    }
    dataDeletionDetectionPolicy = @{
        "@odata.type" = "#Microsoft.Azure.Search.NativeBlobSoftDeleteDeletionDetectionPolicy"
    }
} | ConvertTo-Json -Depth 5 -Compress

$dsBody | Out-File "$env:TEMP\ds_body.json" -Encoding utf8
az rest --method put --url "$SEARCH/datasources/$TEAM-tsg-blob?api-version=2024-07-01" --resource "https://search.azure.com" --body "@$env:TEMP\ds_body.json" --headers "Content-Type=application/json" -o none 2>$null

# ── TSG Pull Indexer ────────────────────────────────────────────────────────
Write-Host "  Creating pull indexer: $TEAM-tsg-indexer"
$indexerBody = @{
    name = "$TEAM-tsg-indexer"
    dataSourceName = "$TEAM-tsg-blob"
    targetIndexName = $TSG_INDEX
    fieldMappings = @(
        @{ sourceFieldName = "title"; targetFieldName = "title" }
        @{ sourceFieldName = "metadata_storage_name"; targetFieldName = "filepath" }
        @{ sourceFieldName = "content"; targetFieldName = "content" }
        @{ sourceFieldName = "keywords"; targetFieldName = "keywords" }
        @{ sourceFieldName = "base64_images"; targetFieldName = "base64_images" }
        @{ sourceFieldName = "title_vector"; targetFieldName = "title_vector" }
        @{ sourceFieldName = "content_vector"; targetFieldName = "content_vector" }
        @{ sourceFieldName = "tsg_description"; targetFieldName = "tsg_description" }
    )
    parameters = @{
        batchSize = 10
        maxFailedItems = -1
        configuration = @{ parsingMode = "json" }
    }
    schedule = @{ interval = "P1D" }
} | ConvertTo-Json -Depth 5 -Compress

$indexerBody | Out-File "$env:TEMP\indexer_body.json" -Encoding utf8
az rest --method put --url "$SEARCH/indexers/$TEAM-tsg-indexer?api-version=2024-07-01" --resource "https://search.azure.com" --body "@$env:TEMP\indexer_body.json" --headers "Content-Type=application/json" -o none 2>$null

Write-Host "  Search setup complete." -ForegroundColor Green
