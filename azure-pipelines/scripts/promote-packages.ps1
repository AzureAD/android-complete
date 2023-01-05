Param (
    [Parameter(Mandatory = $true)][String]$PackagingPAT,
    [Parameter(Mandatory = $false)][String]$PackageVersion = "",
    [Parameter(Mandatory = $false)][String]$common4jVersion = $PackageVersion,
    [Parameter(Mandatory = $false)][String]$commonVersion = $PackageVersion,
    [Parameter(Mandatory = $false)][String]$broker4jVersion = $PackageVersion,
    [Parameter(Mandatory = $false)][String]$adAccountsVersion = $PackageVersion,
    [Parameter(Mandatory = $false)][String]$msalVersion = $PackageVersion,
    [Parameter(Mandatory = $false)][String]$adalVersion = $PackageVersion,
    [Parameter(Mandatory = $false)][String]$PromoteToView = "Prerelease",
    [Parameter(Mandatory = $false)][String]$Organization = "identitydivision",
    [Parameter(Mandatory = $false)][String]$FeedName="AndroidADAL"
)

#request uri
$baseUri = "https://pkgs.dev.azure.com/$Organization";
$promotePackagesApi = "_apis/packaging/feeds/$FeedName/maven/packagesbatch?api-version=7.1-preview.1"
$promotePackagesUri = "$baseUri/$promotePackagesApi"

# Auth header
$base64AuthInfo = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(("token:{0}" -f $PackagingPAT)))
$authHeader = @{Authorization = ("Basic {0}" -f $base64AuthInfo)};

# Request Body
$MavenPackagesBatchRequest = New-Object PSObject -Property @{
        data = @{ viewId = $PromoteToView }
        operation = "promote"
        packages = @(
            @{ artifact = "common4j"; group = "com.microsoft.identity"; version = $common4jVersion},
            @{ artifact = "common"; group = "com.microsoft.identity"; version = $commonVersion},
            @{ artifact = "broker4j"; group = "com.microsoft.identity"; version = $broker4jVersion}
            @{ artifact = "ad-accounts-for-android"; group = "com.microsoft.workplace"; version = $adAccountsVersion}
            @{ artifact = "msal"; group = "com.microsoft.identity.client"; version = $msalVersion}
            @{ artifact = "adal"; group = "com.microsoft.aad"; version = $adalVersion}
        )
   }

$requestBody = $MavenPackagesBatchRequest | ConvertTo-Json

# Call ADO Rest API
try {
    $Result = Invoke-RestMethod -Uri $promotePackagesUri -Method Post -ContentType "application/json" -Headers $authHeader -Body $requestBody;
} catch {
    if($_.ErrorDetails.Message){
        Write-Error $_.ErrorDetails.Message
    }
    throw $_.Exception
}