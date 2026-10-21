# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

function Get-MemoryStorageReviewMetadata {
    param([AllowNull()][AllowEmptyString()][string]$Body)

    $ruleId = $null
    $screened = $false
    $revision = "unknown"
    $fenceCharacter = $null
    $fenceLength = 0

    foreach ($line in ($Body -split '\r?\n')) {
        if ($null -ne $fenceCharacter) {
            if ($line -cmatch ('^ {0,3}' + [regex]::Escape($fenceCharacter) + '{' + $fenceLength + ',}[ \t]*$')) {
                $fenceCharacter = $null
            }
            continue
        }
        if ($line -cmatch '^ {0,3}(`{3,}|~{3,})') {
            $fenceCharacter = $Matches[1].Substring(0, 1)
            $fenceLength = $Matches[1].Length
            continue
        }
        if ($line -cmatch '^Review rule: memory-storage/v1[ \t]*$') {
            $ruleId = "memory-storage/v1"
        }
        if ($line -cmatch '^Review screen: memory-storage/v1[ \t]*$') {
            $screened = $true
        }
        if ($line -cmatch '^Guidance revision: ([0-9a-f]{40}|local-uncommitted|unknown)[ \t]*$') {
            $revision = $Matches[1]
        }
    }

    [PSCustomObject]@{
        RuleId = $ruleId
        MemoryStorageScreened = $screened
        GuidanceRevision = $revision
    }
}
