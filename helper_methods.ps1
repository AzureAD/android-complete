
function Get-VNextHeader {
    param(
        [Hashtable]$changelogConstants
    )
    return "{0}`r`n{1}" -f $changelogConstants["VnextFormat"], $changelogConstants["separator"]
}

function Get-ReplacementtHeader {
    param(
        [Hashtable]$changelogConstants,
        [String]$newVersion = "",
        [string]$newCommonVersion
    )
    $changelogHeader = "{0}`r`n{1}" -f $changelogConstants["VnextFormat"], $changelogConstants["separator"]
    $newVersionHeader =  "{0}{1}`r`n{2}" -f $changelogConstants["versionFormat"], $newVersion, $changelogConstants["separator"]
    $vnextAndNewHeader = "{0}`r`n`r`n{1}" -f $changelogHeader, $newVersionHeader

    # Check if the newCommonVersion is not empty, if not add common update to the changelog
    if ($newCommonVersion -ne "") {
        # Append the common version used
        return "$vnextAndNewHeader`r`n- [PATCH] Update common @$newCommonVersion"
    } else {
        return $vnextAndNewHeader
    }
}

function Update-VersionNumber {
    param(
        [string]$newVersion,
        [string]$versioningFile
    )

    if (-not (Test-Path $versioningFile -PathType Leaf)) {
        Write-Host "Input file '$versioningFile' not found."
        return
    }

    $searchPattern = 'versionName=.*'

    (Get-Content $versioningFile) | ForEach-Object {
        $_ -replace $searchPattern, "versionName=$newVersion"
    } | Set-Content $versioningFile

    Write-Host "Version updated to $newVersion in '$versioningFile'."
}

function Update-ChangelogHeader {
    param(
        [Hashtable]$changelogConstants,
        [string]$newVersion,
        [string]$newCommonVersion = "",
        [string]$changelogFile
    )

    
    if (-not (Test-Path $changelogFile -PathType Leaf)) {
        Write-Host "Input file '$changelogFile' not found."
        return
    }
    # search this haeder to be replaced
    $changelogHeader = Get-VNextHeader  -changelogConstants $changelogConstants 

    #replace with this new header
    $replacementString = Get-ReplacementtHeader -changelogConstants $changelogConstants -newVersion $newVersion -newCommonVersion $newCommonVersion 


    # Read the content of the file
    $fileContent = Get-Content -Path $changelogFile -Raw

    # Use regex with single-line mode to find the multiline string
    if ($fileContent -match "(?s)$changelogHeader") {
        # Replace the matched multiline string
        $newContent = $fileContent -replace "(?s)$changelogHeader", $replacementString

        # Write the updated content back to the file
        Set-Content -Path $changelogFile -Value $newContent -NoNewline

        Write-Host "$changelogFile updated successfully."
    }
    else {
        Write-Host "Pattern not found in the $changelogFile"
    }
}

function Update-GradeFile {
    param(
        [string]$newVersion,
        [string]$gradleFile,
        [string]$variableToUpdate
    )

    if (-not (Test-Path $gradleFile -PathType Leaf)) {
        Write-Host "Input file '$gradleFile' not found."
        return
    }

    $searchPattern = "def $variableToUpdate\s*=\s*`"([^`"]*)`""

    (Get-Content $gradleFile) | ForEach-Object {
        $_ -replace $searchPattern, "def $variableToUpdate = `"$newVersion`""
    } | Set-Content $gradleFile

    Write-Host "$variableToUpdate updated to $newVersion in '$gradleFile'."
}

function Update-AllRCVersionsInFile {
    param(
        [string]$filePath,
        [string]$newRCVersion
    )

    # Read the content from the file
    $content = Get-Content -Path $filePath -Raw

    # Define the regular expression pattern
    $pattern = '(\d+\.\d+\.\d+-RC)(\d+)'

    # Match all occurrences of the pattern in the content
    $rcMatches = [regex]::Matches($content, $pattern)

    if ($rcMatches.Count -gt 0) {
        foreach ($match in $rcMatches) {
            $baseVersion = $match.Groups[1].Value  # Capturing group 1: "0.0.0-RC"
            #$currentRCNumber = [int]$match.Groups[2].Value  # Capturing group 2: RC number

            # Create the updated version string
            $updatedVersion = "$baseVersion$newRCVersion"

            # Replace the old version with the updated version
            $content = $content -replace [regex]::Escape($match.Value), $updatedVersion
        }

        # Write the updated content back to the file
        Set-Content -Path $filePath -Value $content -NoNewline

        Write-Host "Updated all versions in file to: $updatedVersion"
    } else {
        Write-Host "No matches found for the pattern in the file."
    }
}

function Remove-AllRCVersionsInFile {
    param(
        [string]$filePath
    )
    # Read the content from the file
    $content = Get-Content -Path $filePath -Raw

    # Define the regular expression pattern
    $pattern = '(\d+\.\d+\.\d+)(-RC\d+)'

    # Match all occurrences of the pattern in the content
    $rcMatches = [regex]::Matches($content, $pattern)

    if ($rcMatches.Count -gt 0) {
        foreach ($match in $rcMatches) {
            $baseVersion = $match.Groups[1].Value  # Capturing group 1: "0.0.0"
            # Replace the old version with the updated version
            $content = $content -replace [regex]::Escape($match.Value), $baseVersion
        }

        # Write the updated content back to the file
        Set-Content -Path $filePath -Value $content -NoNewline

        Write-Host "Updated all versions in file to: $ $baseVersion"
    } else {
        Write-Host "No matches found for the pattern in the file."
    }
}

