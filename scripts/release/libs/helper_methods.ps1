
function Get-VNextHeader {
    param(
        [Hashtable]$changelogConstants
    )
    return "{0}{1}{2}" -f $changelogConstants["VnextFormat"], [System.Environment]::NewLine, $changelogConstants["separator"]

}

function Get-ReplacementtHeader {
    param(
        [Hashtable]$changelogConstants,
        [String]$newVersion = "",
        [string]$newCommonVersion
    )
    $changelogHeader = "{0}{1}{2}" -f $changelogConstants["VnextFormat"], [System.Environment]::NewLine, $changelogConstants["separator"]
    $newVersionHeader = "{0}{1}{2}{3}" -f $changelogConstants["versionFormat"], $newVersion, [System.Environment]::NewLine, $changelogConstants["separator"]
    $vnextAndNewHeader = "{0}{1}{1}{2}" -f $changelogHeader, [System.Environment]::NewLine, $newVersionHeader    

    # Check if the newCommonVersion is not empty, if not add common update to the changelog
    if ($newCommonVersion -ne "") {
        # Append the common version used
        return "$vnextAndNewHeader$([System.Environment]::NewLine)- [PATCH] Update common @$newCommonVersion"
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
        Write-Host "Input file '$versioningFile' not found."  -ForegroundColor Red
        return
    }

    $searchPattern = 'versionName=.*'

    (Get-Content $versioningFile) | ForEach-Object {
        $_ -replace $searchPattern, "versionName=$newVersion"
    } | Set-Content $versioningFile

    $results = Select-String -Path $versioningFile -Pattern $searchPattern
    if ($results) {
        Write-Host "$versioningFile updated successfully."
    } else {
        Write-Host "No match found on $searchPattern" -ForegroundColor Red
    }
    
}

function Update-ChangelogHeader {
    param(
        [Hashtable]$changelogConstants,
        [string]$newVersion,
        [string]$newCommonVersion = "",
        [string]$changelogFile
    )

    
    if (-not (Test-Path $changelogFile -PathType Leaf)) {
        Write-Host "Input file '$changelogFile' not found." -ForegroundColor Red
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
        Write-Host "Pattern ($changelogHeader) not found in the $changelogFile, File format was not changed."  -ForegroundColor Red
    }
}

function Update-ChangelogHeaderForHotfix {
    param(
        [Hashtable]$changelogConstants,
        [string]$newVersion,
        [string]$newCommonVersion = "",
        [string]$changelogFile
    )

    if (-not (Test-Path $changelogFile -PathType Leaf)) {
        Write-Host "Input file '$changelogFile' not found." -ForegroundColor Red
        return
    }

    # Read the content of the file
    $fileContent = Get-Content -Path $changelogFile -Raw

    # Create the new version header
    $newVersionHeader = $changelogConstants["versionFormat"] -f $newVersion
    
    # Create the entry content
    $entryContent = "- [PATCH] Update common @$newCommonVersion"
    
    # Complete new section with proper newlines
    $newSection = "$newVersionHeader`r`n$($changelogConstants["separator"])`r`n$entryContent`r`n`r`n"

    # Find the first version section pattern (Version X.X.X followed by separator) - only match the FIRST occurrence
    $versionPattern = "(?m)^Version \d+\.\d+\.\d+"
    
    if ($fileContent -match $versionPattern) {
        # Get the position of the first match
        $match = [regex]::Match($fileContent, $versionPattern)
        if ($match.Success) {
            # Insert the new section before the first existing version
            $beforeMatch = $fileContent.Substring(0, $match.Index)
            $afterMatch = $fileContent.Substring($match.Index)
            $newContent = $beforeMatch + $newSection + $afterMatch
            
            # Write the updated content back to the file
            Set-Content -Path $changelogFile -Value $newContent -NoNewline

            Write-Host "$changelogFile updated successfully with hotfix version $newVersion."
        }
    }
    else {
        Write-Host "Could not find existing version pattern in $changelogFile. File format may be different than expected." -ForegroundColor Red
    }
}

function Update-ChangelogHeaderForCommonHotfix {
    param(
        [Hashtable]$changelogConstants,
        [string]$newVersion,
        [string]$changelogFile
    )

    if (-not (Test-Path $changelogFile -PathType Leaf)) {
        Write-Host "Input file '$changelogFile' not found." -ForegroundColor Red
        return
    }

    # Read the content of the file
    $fileContent = Get-Content -Path $changelogFile -Raw

    # Create the new version header with no entries
    $newVersionHeader = "{0}{1}{2}" -f $changelogConstants["versionFormat"], $newVersion, [System.Environment]::NewLine, $changelogConstants["separator"]
    
    # Complete new section (just header and separator, no entries)
    $newSection = "$newVersionHeader$($changelogConstants["separator"])$([System.Environment]::NewLine)$([System.Environment]::NewLine)"

    # Find the first version section pattern (Version X.X.X followed by separator)
    $versionPattern = "(?m)^(Version \d+\.\d+\.\d+)\s*$\s*^([-=]{5,})\s*$"
    
    if ($fileContent -match $versionPattern) {
        # Insert the new section before the first existing version
        $newContent = $fileContent -replace $versionPattern, "$newSection`$1`$([System.Environment]::NewLine)`$2"
        
        # Write the updated content back to the file
        Set-Content -Path $changelogFile -Value $newContent -NoNewline

        Write-Host "$changelogFile updated successfully with hotfix version $newVersion (header only)."
    }
    else {
        Write-Host "Could not find existing version pattern in $changelogFile. File format may be different than expected." -ForegroundColor Red
    }
}

function Update-GradeFile {
    param(
        [string]$newVersion,
        [string]$gradleFile,
        [string]$variableToUpdate
    )

    if (-not (Test-Path $gradleFile -PathType Leaf)) {
        Write-Host "Input file '$gradleFile' not found."  -ForegroundColor Red
        return
    }

    $searchPattern = "def $variableToUpdate\s*=\s*`"([^`"]*)`""

    (Get-Content $gradleFile) | ForEach-Object {
        $_ -replace $searchPattern, "def $variableToUpdate = `"$newVersion`""
    } | Set-Content $gradleFile
    $results = Select-String -Path $gradleFile -Pattern $searchPattern
    if ($results) {
        Write-Host "$gradleFile updated successfully."
    } else {
        Write-Host "No match found for $variableToUpdate on $gradleFile" -ForegroundColor Red
    }
    
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
            Write-Host "$filePath  $match change to $updatedVersion"
        }

        # Write the updated content back to the file
        Set-Content -Path $filePath -Value $content -NoNewline
    } else {
        Write-Host "No matches found in $filePath, File format was changed." -ForegroundColor Red
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

        Write-Host "Updated all versions in $filePat to  $baseVersion"
    } else {
        Write-Host "No matches found for the pattern in $filePath." -ForegroundColor Red
    }
}

