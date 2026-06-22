# assign-roles.ps1
#
# Run this ONCE after deploying the Container App to grant the managed identity
# the permissions it needs to call Azure Search and Azure OpenAI.
#
# Usage:
#   .\assign-roles.ps1 `
#     -PrincipalId  "<identityPrincipalId from bicep output>" `
#     -SearchResourceGroup  "rg-search" `
#     -SearchServiceName    "msalandroiddricopilotsearch" `
#     -OpenAIResourceGroup  "rg-openai" `
#     -OpenAIAccountName    "msal-android-dri-copilot-oai"

param(
    [Parameter(Mandatory = $true)]
    [string]$PrincipalId,

    [Parameter(Mandatory = $true)]
    [string]$SearchResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$SearchServiceName,

    [Parameter(Mandatory = $true)]
    [string]$OpenAIResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$OpenAIAccountName
)

$subscriptionId = az account show --query id -o tsv

$searchScope = "/subscriptions/$subscriptionId/resourceGroups/$SearchResourceGroup/providers/Microsoft.Search/searchServices/$SearchServiceName"
$openAIScope  = "/subscriptions/$subscriptionId/resourceGroups/$OpenAIResourceGroup/providers/Microsoft.CognitiveServices/accounts/$OpenAIAccountName"

Write-Host "Assigning 'Search Index Data Reader' on $SearchServiceName ..."
az role assignment create `
    --assignee-object-id $PrincipalId `
    --assignee-principal-type ServicePrincipal `
    --role "Search Index Data Reader" `
    --scope $searchScope

Write-Host "Assigning 'Cognitive Services OpenAI User' on $OpenAIAccountName ..."
az role assignment create `
    --assignee-object-id $PrincipalId `
    --assignee-principal-type ServicePrincipal `
    --role "Cognitive Services OpenAI User" `
    --scope $openAIScope

Write-Host "Done. Role assignments complete."
