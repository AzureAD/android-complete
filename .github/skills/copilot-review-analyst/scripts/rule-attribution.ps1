# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

function Get-MemoryStorageReviewMetadata {
    param([AllowNull()][AllowEmptyString()][string]$Body)

    $ruleId = $null
    $screened = $false
    $revision = "unknown"
    $fenceCharacter = $null
    $fenceLength = 0
    $quotedParagraph = $false
    $quotedFenceCharacter = $null
    $quotedFenceLength = 0
    $paragraphBreak = '^ {0,3}(?:#{1,6}(?:[ \t]|$)|`{3,}|~{3,}|(?:\*[ \t]*){3,}$|(?:-[ \t]*){3,}$|(?:_[ \t]*){3,}$)'

    foreach ($line in ($Body -split '\r?\n')) {
        if ($null -ne $fenceCharacter) {
            if ($line -cmatch ('^ {0,3}' + [regex]::Escape($fenceCharacter) + '{' + $fenceLength + ',}[ \t]*$')) {
                $fenceCharacter = $null
            }
            continue
        }
        if ($line -cmatch '^ {0,3}>[ \t]?(.*)$') {
            $content = $Matches[1]
            while ($content -cmatch '^ {0,3}>[ \t]?(.*)$') {
                $content = $Matches[1]
            }
            if ($null -ne $quotedFenceCharacter) {
                if ($content -cmatch ('^ {0,3}' + [regex]::Escape($quotedFenceCharacter) + '{' + $quotedFenceLength + ',}[ \t]*$')) {
                    $quotedFenceCharacter = $null
                }
                continue
            }
            if ($content -cmatch '^ {0,3}(`{3,}|~{3,})') {
                $quotedFenceCharacter = $Matches[1].Substring(0, 1)
                $quotedFenceLength = $Matches[1].Length
                $quotedParagraph = $false
                continue
            }
            $quotedParagraph = $content -notmatch '^[ \t]*$' -and
                $content -notmatch $paragraphBreak -and
                -not ($quotedParagraph -and $content -match '^ {0,3}=+[ \t]*$') -and
                -not (-not $quotedParagraph -and $content -match '^(?: {4}|\t)')
            continue
        }
        # Only paragraph content can continue a blockquote without a > prefix.
        if ($quotedParagraph -and $line -notmatch '^[ \t]*$' -and
            $line -notmatch $paragraphBreak -and $line -notmatch '^ {0,3}(?:[-+*]|1[.)])[ \t]+') {
            continue
        }
        $quotedParagraph = $false
        $quotedFenceCharacter = $null
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
