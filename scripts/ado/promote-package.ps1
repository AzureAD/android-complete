# Define parameters
param (
    [string]$organization = "identitydivision",
    [string]$project = "Engineering",
    [string]$feed = "AndroidADAL",
    [string]$packageName,
    [string]$packageVersion, 
    [string]$packageType = "maven",  # Change this for nuget, maven, etc.
    [string]$targetView = "Prerelease",  # The target view to promote to
    [string]$personalAccessToken 
)

# Construct API URL
$apiUrl = "https://feeds.dev.azure.com/$organization/$project/_apis/packaging/feeds/$feed/$packageType/packages/$packageName/versions/$packageVersion/views/$targetView?api-version=6.0-preview.1"

# Encode PAT for authentication
$base64AuthInfo = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(":$personalAccessToken"))

# Make API request
$response = Invoke-RestMethod -Uri $apiUrl -Method Put -Headers @{ 
    Authorization = "Basic $base64AuthInfo"
    Accept = "application/json"
} -ContentType "application/json"

# Output response
$response
