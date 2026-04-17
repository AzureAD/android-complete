# ADO Test Plan API Reference

## Table of Contents
- [Authentication](#authentication)
- [List Suites in a Plan](#list-suites)
- [Get Test Cases from a Suite](#get-test-cases)
- [Read a Test Case (Steps)](#read-test-case)
- [Create a Test Suite](#create-suite)
- [Create a Test Case Work Item](#create-test-case)
- [Add Test Cases to a Suite](#add-to-suite)
- [Steps XML Format](#steps-xml)

## Authentication

```powershell
$pat = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv 2>$null
$headers = @{Authorization="Bearer $pat"; "Content-Type"="application/json"}
```

## List Suites

```powershell
$url = "https://identitydivision.visualstudio.com/Engineering/_apis/testplan/Plans/{planId}/Suites?api-version=7.1"
$resp = Invoke-RestMethod -Uri $url -Headers $headers
$resp.value | ForEach-Object { "$($_.id): $($_.name)" }
```

Key plan IDs:
- Master plan: `2007357`
- Monthly release plan: `3504766` (changes each month — check current)

Key suite IDs (Manual Tests - Android Broker):
- Master plan: `2008656`
- Monthly plan: `3504780`

## Get Test Cases

```powershell
$url = "https://identitydivision.visualstudio.com/Engineering/_apis/testplan/Plans/{planId}/Suites/{suiteId}/TestCase?api-version=7.1"
$resp = Invoke-RestMethod -Uri $url -Headers $headers
$resp.value | ForEach-Object { "$($_.workItem.id): $($_.workItem.name)" }
```

## Read Test Case

Use the ADO MCP tool or REST API:

```powershell
# Via MCP tool:
mcp_ado_wit_get_work_item(id=<testCaseId>, project="Engineering")

# The steps are in: fields.'Microsoft.VSTS.TCM.Steps'
```

## Create Suite

```powershell
$body = @{
    suiteType = "StaticTestSuite"
    name = "Suite Name"
    parentSuite = @{id = <parentSuiteId>}
} | ConvertTo-Json

$url = "https://identitydivision.visualstudio.com/Engineering/_apis/testplan/Plans/{planId}/Suites?api-version=7.1"
$resp = Invoke-RestMethod -Uri $url -Headers $headers -Method Post -Body $body
# Returns: $resp.id = new suite ID
```

## Create Test Case

```powershell
$headers_patch = @{Authorization="Bearer $pat"; "Content-Type"="application/json-patch+json"}

$body = @(
    @{op="add"; path="/fields/System.Title"; value="[Feature] Test case title"},
    @{op="add"; path="/fields/System.AreaPath"; value="Engineering\Auth Client\Broker\Android"},
    @{op="add"; path="/fields/Microsoft.VSTS.TCM.Steps"; value=$stepsXml},
    @{op="add"; path="/fields/System.Tags"; value="Android; Broker; FeatureName"}
) | ConvertTo-Json -Depth 5

$url = "https://identitydivision.visualstudio.com/Engineering/_apis/wit/workitems/`$Test%20Case?api-version=7.1"
$resp = Invoke-RestMethod -Uri $url -Headers $headers_patch -Method Post -Body $body
# Returns: $resp.id = new test case work item ID
```

## Add to Suite

```powershell
$tcIds = @(111, 222, 333)
$body = $tcIds | ForEach-Object { @{workItem=@{id=$_}} } | ConvertTo-Json -AsArray

$url = "https://identitydivision.visualstudio.com/Engineering/_apis/testplan/Plans/{planId}/Suites/{suiteId}/TestCase?api-version=7.1"
$resp = Invoke-RestMethod -Uri $url -Headers $headers -Method Post -Body $body
# Returns: $resp.value.Count = number added
```

## Steps XML Format

ADO test case steps use this XML structure:

```xml
<steps id="0" last="{totalSteps}">
  <step id="1" type="ActionStep">
    <parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;Action text here&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>
    <parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;Expected result here&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>
    <description/>
  </step>
  <step id="2" type="ValidateStep">
    <parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;Action with validation&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>
    <parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;What to verify&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>
    <description/>
  </step>
</steps>
```

- `ActionStep`: Setup/navigation steps (expected result can be empty)
- `ValidateStep`: Steps where an outcome must be verified
- Text must be HTML-encoded (`<` → `&lt;`, `>` → `&gt;`, etc.)

### PowerShell Helper for Building Steps

```powershell
function MakeStep($id, $type, $action, $expected) {
    $a = [System.Web.HttpUtility]::HtmlEncode($action)
    $e = [System.Web.HttpUtility]::HtmlEncode($expected)
    return "<step id=`"$id`" type=`"$type`"><parameterizedString isformatted=`"true`">&lt;DIV&gt;&lt;P&gt;$a&lt;/P&gt;&lt;/DIV&gt;</parameterizedString><parameterizedString isformatted=`"true`">&lt;DIV&gt;&lt;P&gt;$e&lt;/P&gt;&lt;/DIV&gt;</parameterizedString><description/></step>"
}

# Usage:
$stepsXml = "<steps id=`"0`" last=`"3`">"
$stepsXml += MakeStep 1 "ActionStep" "Install Authenticator" "Authenticator installed"
$stepsXml += MakeStep 2 "ActionStep" "Sign in with AAD account in Outlook" "Sign-in succeeds"
$stepsXml += MakeStep 3 "ValidateStep" "Open Chrome, navigate to outlook.com" "User sees account picker with SSO"
$stepsXml += "</steps>"
```
