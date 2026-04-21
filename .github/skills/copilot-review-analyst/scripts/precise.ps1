<#
.SYNOPSIS
    Precise verification: For every no-response Copilot comment, check
    if the specific suggestion was applied or the exact line range was modified.
    
    Approach:
    1. For each comment, get the diff between comment's commit and PR head
    2. Check if the diff for the commented file has hunks overlapping the 
       comment's line range (strong signal: the exact lines were modified)
    3. For suggestion blocks, additionally check if key code tokens from 
       the suggestion appear as additions (+lines) in the diff
#>

$ErrorActionPreference = "Continue"
$OutputDir = "$env:TEMP\copilot-review-analysis"

$COPILOT_USERS = @("Copilot", "copilot-pull-request-reviewer[bot]")

$repoSlugs = @{
    "common" = "AzureAD/microsoft-authentication-library-common-for-android"
    "msal"   = "AzureAD/microsoft-authentication-library-for-android"
    "broker" = "identity-authnz-teams/ad-accounts-for-android"
}

# Load raw data
$rawData = Get-Content "$OutputDir\raw_results.json" | ConvertFrom-Json
$noResponse = $rawData | Where-Object { $_.HasReply -eq $false }
Write-Host "Total no-response comments to verify: $($noResponse.Count)" -ForegroundColor Cyan

# Caches
$prCommentApiCache = @{}
$prHeadCache = @{}
$diffCache = @{}  # "repo/pr/commitA...commitB" -> diff data per file

function Get-PRComments($repo, $prNum) {
    $key = "$repo/$prNum"
    if (-not $prCommentApiCache.ContainsKey($key)) {
        $slug = $script:repoSlugs[$repo]
        try {
            $raw = gh api "repos/$slug/pulls/$prNum/comments" --paginate 2>&1
            $prCommentApiCache[$key] = $raw | ConvertFrom-Json
        } catch {
            $prCommentApiCache[$key] = @()
        }
    }
    return $prCommentApiCache[$key]
}

function Get-PRHead($repo, $prNum) {
    $key = "$repo/$prNum"
    if (-not $prHeadCache.ContainsKey($key)) {
        $slug = $script:repoSlugs[$repo]
        try {
            $data = gh api "repos/$slug/pulls/$prNum" --jq '.head.sha' 2>&1
            $prHeadCache[$key] = $data.Trim()
        } catch {
            $prHeadCache[$key] = ""
        }
    }
    return $prHeadCache[$key]
}

function Get-FileDiff($repo, $prNum, $commitA, $commitB, $filePath) {
    $slug = $script:repoSlugs[$repo]
    $cacheKey = "$repo/$prNum/$commitA/$commitB"
    
    if (-not $diffCache.ContainsKey($cacheKey)) {
        try {
            # Get the entire compare result (all files)
            $rawJson = gh api "repos/$slug/compare/${commitA}...${commitB}" 2>&1
            $compareData = $rawJson | ConvertFrom-Json
            
            # Build a hash of file -> patch data
            $fileDiffs = @{}
            foreach ($f in $compareData.files) {
                $fileDiffs[$f.filename] = @{
                    Status = $f.status
                    Patch = $f.patch
                    Additions = $f.additions
                    Deletions = $f.deletions
                    Changes = $f.changes
                }
            }
            $diffCache[$cacheKey] = $fileDiffs
        } catch {
            $diffCache[$cacheKey] = @{}
        }
    }
    
    $diffs = $diffCache[$cacheKey]
    if ($diffs.ContainsKey($filePath)) {
        return $diffs[$filePath]
    }
    return $null
}

function Extract-SuggestionTokens($body) {
    # Extract key tokens from suggestion blocks for matching against diff
    $pattern = '(?s)```suggestion\r?\n(.*?)```'
    $m = [regex]::Match($body, $pattern)
    if (-not $m.Success) { return @() }
    
    $sugCode = $m.Groups[1].Value
    $lines = $sugCode -split "`n" | ForEach-Object { $_.TrimEnd("`r").Trim() }
    
    # Keep significant lines (not blank, not just punctuation)
    $tokens = @()
    foreach ($line in $lines) {
        if ($line.Length -le 3) { continue }
        if ($line -match '^\s*[\{\}\(\)\;\*\/]+\s*$') { continue }
        if ($line -match '^\s*$') { continue }
        
        # Extract distinctive tokens: identifiers, strings, method calls
        # We just use the trimmed line content as a token  
        $tokens += $line
    }
    return $tokens
}

function Check-DiffHunksOverlap($patchText, $commentStartLine, $commentEndLine) {
    # Parse diff hunk headers to find which original line ranges were modified
    if (-not $patchText) { return $false }
    
    $hunkPattern = '@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@'
    $hunks = [regex]::Matches($patchText, $hunkPattern)
    
    $window = 5  # Allow +-5 lines of drift
    $rangeStart = [Math]::Max(1, $commentStartLine - $window)
    $rangeEnd = $commentEndLine + $window
    
    foreach ($hunk in $hunks) {
        $oldStart = [int]$hunk.Groups[1].Value
        $oldCount = if ($hunk.Groups[2].Success) { [int]$hunk.Groups[2].Value } else { 1 }
        $oldEnd = $oldStart + $oldCount - 1
        
        if ($oldEnd -ge $rangeStart -and $oldStart -le $rangeEnd) {
            return $true
        }
    }
    return $false
}

function Check-SuggestionInDiff($patchText, $suggestionTokens) {
    # Check if key tokens from the suggestion appear as additions in the diff
    if (-not $patchText -or $suggestionTokens.Count -eq 0) { return 0 }
    
    # Extract added lines from the diff
    $addedLines = @()
    foreach ($line in ($patchText -split "`n")) {
        if ($line.StartsWith("+") -and -not $line.StartsWith("+++")) {
            $addedLines += $line.Substring(1).Trim()
        }
    }
    
    if ($addedLines.Count -eq 0) { return 0 }
    
    $matchCount = 0
    foreach ($token in $suggestionTokens) {
        $normalizedToken = $token.Trim().ToLower()
        if ($normalizedToken.Length -lt 5) { continue }
        
        foreach ($addedLine in $addedLines) {
            if ($addedLine.ToLower().Contains($normalizedToken)) {
                $matchCount++
                break
            }
        }
    }
    
    return $matchCount
}

# ========================================
# MAIN ANALYSIS LOOP
# ========================================
$results = @()
$count = 0

foreach ($item in $noResponse) {
    $count++
    $repo = $item.Repo
    $prNum = $item.PRNumber
    $commentId = $item.CommentId
    $filePath = $item.FilePath
    $body = $item.CommentBody
    $hasSuggestion = $body -match '```suggestion'
    
    # Get full API data for this comment
    $allComments = Get-PRComments $repo $prNum
    $apiComment = $allComments | Where-Object { $_.id -eq $commentId }
    
    if ($null -eq $apiComment) {
        $results += [PSCustomObject]@{
            Repo = $repo; PRNumber = $prNum; CommentId = $commentId
            FilePath = $filePath; PRAuthor = $item.PRAuthor
            HasSuggestion = $hasSuggestion; Verdict = "unknown"
            Evidence = "Comment not found in API"; CommentExcerpt = ""
        }
        continue
    }
    
    $commitId = $apiComment.commit_id
    $commentLine = if ($apiComment.line) { [int]$apiComment.line } else { 0 }
    $commentStartLine = if ($apiComment.start_line) { [int]$apiComment.start_line } else { $commentLine }
    if ($commentStartLine -eq 0) { $commentStartLine = $commentLine }
    
    $headSha = Get-PRHead $repo $prNum
    
    if (-not $headSha -or -not $commitId -or $headSha -eq $commitId) {
        $results += [PSCustomObject]@{
            Repo = $repo; PRNumber = $prNum; CommentId = $commentId
            FilePath = $filePath; PRAuthor = $item.PRAuthor
            HasSuggestion = $hasSuggestion; Verdict = "no-subsequent-commits"
            Evidence = "Copilot commented on final commit (no commits after review)"
            CommentExcerpt = ""
        }
        continue
    }
    
    # Get the diff for this file between comment commit and PR head
    $fileDiff = Get-FileDiff $repo $prNum $commitId $headSha $filePath
    
    if ($null -eq $fileDiff) {
        # File was NOT modified after the comment commit
        $results += [PSCustomObject]@{
            Repo = $repo; PRNumber = $prNum; CommentId = $commentId
            FilePath = $filePath; PRAuthor = $item.PRAuthor
            HasSuggestion = $hasSuggestion; Verdict = "file-not-changed"
            Evidence = "File was not modified in any commit after Copilot's review"
            CommentExcerpt = ""
        }
        continue
    }
    
    $patchText = $fileDiff.Patch
    
    if ($hasSuggestion) {
        # === SUGGESTION BLOCK: check if suggestion tokens appear in diff additions ===
        $tokens = Extract-SuggestionTokens $body
        $tokenMatchCount = Check-SuggestionInDiff $patchText $tokens
        $totalTokens = ($tokens | Where-Object { $_.Trim().Length -ge 5 }).Count
        if ($totalTokens -eq 0) { $totalTokens = 1 }  # avoid div by zero
        $tokenMatchRatio = $tokenMatchCount / $totalTokens
        
        # Also check line-range overlap
        $linesOverlap = $false
        if ($commentLine -gt 0) {
            $linesOverlap = Check-DiffHunksOverlap $patchText $commentStartLine $commentLine
        }
        
        if ($tokenMatchRatio -ge 0.5 -and $linesOverlap) {
            $verdict = "suggestion-applied"
        } elseif ($tokenMatchRatio -ge 0.5) {
            $verdict = "suggestion-likely-applied"
        } elseif ($linesOverlap) {
            $verdict = "lines-modified-different-fix"
        } elseif ($fileDiff.Changes -gt 0) {
            $verdict = "file-changed-elsewhere"
        } else {
            $verdict = "not-applied"
        }
        
        $evidence = "Tokens matched: $tokenMatchCount/$totalTokens ($([math]::Round($tokenMatchRatio*100))%). Lines overlap: $linesOverlap. File changes: +$($fileDiff.Additions)/-$($fileDiff.Deletions)"
        
    } else {
        # === PROSE COMMENT: check if the exact line range was modified ===
        if ($commentLine -eq 0) {
            # No line info - can only tell if file changed
            $verdict = if ($fileDiff.Changes -gt 0) { "file-changed-no-line-info" } else { "not-applied" }
            $evidence = "No line number in comment. File changes: +$($fileDiff.Additions)/-$($fileDiff.Deletions)"
        } else {
            $linesOverlap = Check-DiffHunksOverlap $patchText $commentStartLine $commentLine
            if ($linesOverlap) {
                $verdict = "exact-lines-modified"
            } elseif ($fileDiff.Changes -gt 0) {
                $verdict = "file-changed-elsewhere"
            } else {
                $verdict = "not-applied"
            }
            $evidence = "Comment lines $commentStartLine-$commentLine. Lines overlap: $linesOverlap. File changes: +$($fileDiff.Additions)/-$($fileDiff.Deletions)"
        }
    }
    
    $excerpt = ($body -replace "`n"," " -replace "`r","")
    if ($excerpt.Length -gt 120) { $excerpt = $excerpt.Substring(0, 120) + "..." }
    
    $results += [PSCustomObject]@{
        Repo = $repo; PRNumber = $prNum; CommentId = $commentId
        FilePath = $filePath; PRAuthor = $item.PRAuthor
        HasSuggestion = $hasSuggestion; Verdict = $verdict
        Evidence = $evidence; CommentExcerpt = $excerpt
    }
    
    if ($count % 25 -eq 0) {
        Write-Host "  Processed $count/$($noResponse.Count)..." -ForegroundColor DarkGray
        Start-Sleep -Milliseconds 200
    }
}

# Save
$results | ConvertTo-Json -Depth 5 | Out-File "$OutputDir\precise.json" -Encoding utf8

# ========================================
# STATISTICS
# ========================================
Write-Host ""
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "     PRECISE VERIFICATION RESULTS" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow

# Suggestion block results
$sugResults = $results | Where-Object { $_.HasSuggestion -eq $true }
Write-Host ""
Write-Host "--- SUGGESTION BLOCK COMMENTS ($($sugResults.Count)) ---" -ForegroundColor Cyan
$sugResults | Group-Object Verdict | Sort-Object Count -Descending | ForEach-Object {
    $pct = [math]::Round(($_.Count / [Math]::Max(1,$sugResults.Count)) * 100, 1)
    Write-Host "  $($_.Name): $($_.Count) ($pct%)"
}

# Prose results
$proseResults = $results | Where-Object { $_.HasSuggestion -eq $false }
Write-Host ""
Write-Host "--- PROSE COMMENTS ($($proseResults.Count)) ---" -ForegroundColor Cyan
$proseResults | Group-Object Verdict | Sort-Object Count -Descending | ForEach-Object {
    $pct = [math]::Round(($_.Count / [Math]::Max(1,$proseResults.Count)) * 100, 1)
    Write-Host "  $($_.Name): $($_.Count) ($pct%)"
}

# All combined
Write-Host ""
Write-Host "--- ALL COMMENTS COMBINED ($($results.Count)) ---" -ForegroundColor Cyan
$results | Group-Object Verdict | Sort-Object Count -Descending | ForEach-Object {
    $pct = [math]::Round(($_.Count / $results.Count) * 100, 1)
    Write-Host "  $($_.Name): $($_.Count) ($pct%)"
}

# Strong evidence categories
$strongApplied = @("suggestion-applied", "suggestion-likely-applied", "exact-lines-modified")
$weakApplied = @("lines-modified-different-fix")
$notApplied = @("file-not-changed", "not-applied", "file-changed-elsewhere", "file-changed-no-line-info")
$noData = @("no-subsequent-commits", "unknown")

$strongCount = ($results | Where-Object { $_.Verdict -in $strongApplied }).Count
$weakCount = ($results | Where-Object { $_.Verdict -in $weakApplied }).Count
$notCount = ($results | Where-Object { $_.Verdict -in $notApplied }).Count
$noDataCount = ($results | Where-Object { $_.Verdict -in $noData }).Count

Write-Host ""
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "     EVIDENCE-BASED SUMMARY" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  STRONG: Suggestion applied OR exact lines modified:            $strongCount ($([math]::Round($strongCount/$results.Count*100,1))%)" -ForegroundColor Green
Write-Host "  MODERATE: Lines near comment modified (different fix):          $weakCount ($([math]::Round($weakCount/$results.Count*100,1))%)" -ForegroundColor DarkGreen
Write-Host "  NOT APPLIED: File not changed OR changes elsewhere in file:   $notCount ($([math]::Round($notCount/$results.Count*100,1))%)" -ForegroundColor Red
Write-Host "  NO DATA: No commits after review / unknown:                   $noDataCount ($([math]::Round($noDataCount/$results.Count*100,1))%)" -ForegroundColor DarkGray

# =======================================
# OVERALL SUMMARY
# =======================================
Write-Host ""
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "     DIFF VERIFICATION COMPLETE" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow

$totalAll = $rawData.Count
$replied = ($rawData | Where-Object { $_.HasReply -eq $true }).Count
$noReply = ($rawData | Where-Object { $_.HasReply -eq $false }).Count

Write-Host ""
Write-Host "  Total comments: $totalAll" -ForegroundColor White
Write-Host "  Replied (Phase 3 will classify): $replied" -ForegroundColor White
Write-Host "  No reply (verified via diff):    $noReply" -ForegroundColor White
Write-Host ""
Write-Host "  Of the $noReply no-reply comments:" -ForegroundColor Cyan
Write-Host "    Applied (strong evidence):     $strongCount ($([math]::Round($strongCount/$noReply*100,1))%)" -ForegroundColor Green
Write-Host "    Nearby lines modified:         $weakCount ($([math]::Round($weakCount/$noReply*100,1))%)" -ForegroundColor DarkGreen
Write-Host "    Not applied / no evidence:     $notCount ($([math]::Round($notCount/$noReply*100,1))%)" -ForegroundColor Red
Write-Host "    No subsequent commits:         $noDataCount ($([math]::Round($noDataCount/$noReply*100,1))%)" -ForegroundColor DarkGray

Write-Host ""
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "Data: $OutputDir\precise.json" -ForegroundColor White
Write-Host "Next: Run Phase 3 (AI classification of all replied comments)." -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow
