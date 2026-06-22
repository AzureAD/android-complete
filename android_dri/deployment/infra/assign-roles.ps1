<#
.SYNOPSIS
  Assign RBAC roles to the managed identity for Search + OpenAI.

.PARAMETER PrincipalId
  Object ID of the managed identity.

.PARAMETER SearchEndpoint
  Azure AI Search endpoint (e.g., https://mysearch.search.windows.net)

.PARAMETER OpenAIEndpoint
  Azure OpenAI endpoint (e.g., https://myoai.openai.azure.com/)

.PARAMETER SubscriptionId
  Azure subscription ID.
#>
param(
    [Parameter(Mandatory)] [string]$PrincipalId,
    [Parameter(Mandatory)] [string]$SearchEndpoint,
    [Parameter(Mandatory)] [string]$OpenAIEndpoint,
    [Parameter(Mandatory)] [string]$SubscriptionId
)

$ErrorActionPreference = "Stop"

# Extract resource names from endpoints
$searchName = ([Uri]$SearchEndpoint).Host.Split('.')[0]
$oaiName = ([Uri]$OpenAIEndpoint).Host.Split('.')[0]

# Find resource groups (search across subscription)
Write-Host "Finding resources..."
$searchRG = az search service list --subscription $SubscriptionId --query "[?name=='$searchName'].resourceGroup" -o tsv
$oaiRG = az cognitiveservices account list --subscription $SubscriptionId --query "[?name=='$oaiName'].resourceGroup" -o tsv

$searchScope = "/subscriptions/$SubscriptionId/resourceGroups/$searchRG/providers/Microsoft.Search/searchServices/$searchName"
$oaiScope = "/subscriptions/$SubscriptionId/resourceGroups/$oaiRG/providers/Microsoft.CognitiveServices/accounts/$oaiName"

Write-Host "Assigning 'Search Index Data Reader' on $searchName..."
az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal --role "Search Index Data Reader" --scope $searchScope -o none

Write-Host "Assigning 'Search Index Data Contributor' on $searchName (for indexer writes)..."
az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal --role "Search Index Data Contributor" --scope $searchScope -o none

Write-Host "Assigning 'Cognitive Services OpenAI User' on $oaiName..."
az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" --scope $oaiScope -o none

Write-Host "RBAC assignments complete." -ForegroundColor Green
