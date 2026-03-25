<#
.SYNOPSIS
    Analyze Copilot code review comments across 3 Android Auth repos.
    Classifies comments as helpful vs unhelpful based on human responses.
#>

param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$StartDate = (Get-Date).AddDays(-60).ToString("yyyy-MM-dd")
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ========================================
# HELPER FUNCTION
# ========================================
function ClassifyResponse {
    param($replies)
    
    if ($null -eq $replies -or @($replies).Count -eq 0) {
        return "no-response"
    }
    
    $humanReplyText = (@($replies) | ForEach-Object { $_.body }) -join " "
    $replyLower = $humanReplyText.ToLower()
    
    # Positive signals
    $positivePatterns = @(
        "good catch", "fixed", "done", "addressed", "will fix", "will address",
        "thanks", "thank you", "agreed", "makes sense", "updated", "nice catch",
        "you're right", "you are right", "correct", "valid point", "great catch",
        "resolved", "will do", "good point", "fair point", "acknowledged",
        "applied", "changed", "modified", "yep", "absolutely",
        "i'll update", "i will update", "i'll fix", "i will fix",
        "good suggestion", "great suggestion", "nice suggestion",
        "will change", "will update", "pushed a fix", "committed",
        "good find", "great find", "indeed",
        "making the change", "i've updated", "i've fixed"
    )
    
    # Negative signals
    $negativePatterns = @(
        "not applicable", "n/a", "won't fix", "wontfix", "by design", 
        "intentional", "false positive", "not relevant", "ignore",
        "doesn't apply", "not needed", "unnecessary", "nah", "no need",
        "disagree", "incorrect", "wrong", "not accurate", "hallucin",
        "not a real issue", "not an issue", "this is fine", "it's fine",
        "already handled", "already done", "not applicable here",
        "copilot is wrong", "bot is wrong", "misunderstanding",
        "out of scope", "does not apply", "not a concern", "not a problem",
        "doesn't matter", "won't happen", "can't happen", "impossible"
    )
    
    $isPositive = $false
    $isNegative = $false
    
    foreach ($p in $positivePatterns) {
        if ($replyLower -match [regex]::Escape($p)) {
            $isPositive = $true
            break
        }
    }
    foreach ($n in $negativePatterns) {
        if ($replyLower -match [regex]::Escape($n)) {
            $isNegative = $true
            break
        }
    }
    
    if ($isPositive -and -not $isNegative) {
        return "helpful-acknowledged"
    } elseif ($isNegative -and -not $isPositive) {
        return "unhelpful-dismissed"
    } elseif ($isPositive -and $isNegative) {
        return "mixed-response"
    } else {
        return "replied-unclear"
    }
}

# Copilot uses "Copilot" for inline review comments
$COPILOT_USERS = @("Copilot", "copilot-pull-request-reviewer[bot]")
$BOT_AUTHORS = @("app/copilot-swe-agent", "Copilot", "dependabot[bot]", "github-actions[bot]")

$repos = @(
    @{ Label = "common"; Slug = "AzureAD/microsoft-authentication-library-common-for-android" },
    @{ Label = "msal";   Slug = "AzureAD/microsoft-authentication-library-for-android" },
    @{ Label = "broker"; Slug = "identity-authnz-teams/ad-accounts-for-android" }
)

$allResults = @()
$reviewSummaries = @()

foreach ($repo in $repos) {
    $label = $repo.Label
    $slug = $repo.Slug
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "Processing: $label ($slug)" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    # Load cached PR list
    $prsFile = "$env:TEMP\${label}_prs.json"
    if (-not (Test-Path $prsFile)) {
        Write-Host "  Fetching PR list..."
        gh pr list --repo $slug --state all --limit 200 --json number,title,author,createdAt,state --search "created:>=$StartDate" 2>&1 | Out-File -FilePath $prsFile -Encoding utf8
    }
    
    $allPRs = Get-Content $prsFile | ConvertFrom-Json
    $humanPRs = $allPRs | Where-Object { $_.author.login -notin $BOT_AUTHORS }
    
    Write-Host "  Total PRs: $($allPRs.Count), Human PRs: $($humanPRs.Count)"

    $repoResults = @()
    $prCount = 0
    
    foreach ($pr in $humanPRs) {
        $prCount++
        $prNum = $pr.number
        $prAuthor = $pr.author.login
        $prTitle = $pr.title
        
        # Get ALL review comments (inline code comments) for this PR
        try {
            $commentsRaw = gh api "repos/$slug/pulls/$prNum/comments" --paginate 2>&1
            $comments = $commentsRaw | ConvertFrom-Json
        } catch {
            Write-Host "  PR #$prNum - parse error, skipping" -ForegroundColor Yellow
            continue
        }
        
        if ($null -eq $comments -or $comments.Count -eq 0) {
            # No inline review comments at all
            # Still check if copilot left a review summary
        } else {
            # Find copilot inline comments (top-level, not replies to others)
            $copilotComments = $comments | Where-Object { 
                $_.user.login -in $COPILOT_USERS -and 
                ($null -eq $_.in_reply_to_id -or $_.in_reply_to_id -eq 0 -or $_.in_reply_to_id -eq "")
            }
            
            if ($copilotComments.Count -gt 0) {
                Write-Host "  PR #$prNum ($prAuthor): $($copilotComments.Count) copilot inline comments" -ForegroundColor Green
            }

            foreach ($cc in $copilotComments) {
                $commentId = $cc.id
                $commentBody = $cc.body
                $commentPath = $cc.path
                $commentLine = $cc.line
                
                # Find human replies to this copilot comment
                $replies = $comments | Where-Object { 
                    $_.in_reply_to_id -eq $commentId -and $_.user.login -notin $COPILOT_USERS
                }
                
                # Classify the comment
                $classification = ClassifyResponse -replies $replies
                
                $humanReplyText = ($replies | ForEach-Object { $_.body }) -join " | "

                $repoResults += [PSCustomObject]@{
                    Repo             = $label
                    PRNumber         = $prNum
                    PRAuthor         = $prAuthor
                    PRTitle          = $prTitle
                    PRState          = $pr.state
                    CommentId        = $commentId
                    FilePath         = $commentPath
                    Line             = $commentLine
                    CommentBody      = $commentBody
                    CommentExcerpt   = if ($commentBody.Length -gt 250) { $commentBody.Substring(0, 250) + "..." } else { $commentBody }
                    HumanReplyCount  = $replies.Count
                    HumanReplyText   = if ($humanReplyText.Length -gt 400) { $humanReplyText.Substring(0, 400) + "..." } else { $humanReplyText }
                    Classification   = $classification
                    CommentType      = "inline"
                }
            }
        }
        
        # Also check the review-level summary comments from copilot
        try {
            $reviewsRaw = gh api "repos/$slug/pulls/$prNum/reviews" 2>&1
            $reviews = $reviewsRaw | ConvertFrom-Json
            $copilotReviews = $reviews | Where-Object { $_.user.login -in $COPILOT_USERS -and $_.body.Length -gt 0 }
            
            foreach ($rev in $copilotReviews) {
                $reviewSummaries += [PSCustomObject]@{
                    Repo       = $label
                    PRNumber   = $prNum
                    PRAuthor   = $prAuthor
                    PRTitle    = $prTitle
                    ReviewId   = $rev.id
                    State      = $rev.state
                    BodyExcerpt = if ($rev.body.Length -gt 300) { $rev.body.Substring(0, 300) + "..." } else { $rev.body }
                }
            }
        } catch {
            # Skip if review fetch fails
        }
        
        # Rate limiting
        if ($prCount % 15 -eq 0) {
            Write-Host "  ... processed $prCount/$($humanPRs.Count) PRs" -ForegroundColor DarkGray
            Start-Sleep -Milliseconds 300
        }
    }
    
    Write-Host "  Repo total: $($repoResults.Count) copilot inline comments found" -ForegroundColor Magenta
    $allResults += $repoResults
}

# Save raw results
$allResults | ConvertTo-Json -Depth 5 | Out-File "$OutputDir\raw_results.json" -Encoding utf8
$reviewSummaries | ConvertTo-Json -Depth 5 | Out-File "$OutputDir\review_summaries.json" -Encoding utf8

# ========================================
# STATISTICS
# ========================================
Write-Host "`n`n" -NoNewline
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "           COPILOT CODE REVIEW ANALYSIS RESULTS" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow
$endDate = (Get-Date).ToString("MMM d, yyyy")
$startDateFormatted = [datetime]::Parse($StartDate).ToString("MMM d, yyyy")
Write-Host "Date Range: $startDateFormatted - $endDate" -ForegroundColor White
Write-Host "================================================================`n" -ForegroundColor Yellow

$totalComments = $allResults.Count
Write-Host "TOTAL COPILOT INLINE REVIEW COMMENTS: $totalComments" -ForegroundColor White
Write-Host "TOTAL COPILOT REVIEW SUMMARIES: $($reviewSummaries.Count)`n" -ForegroundColor White

# Unique PRs with copilot reviews
$uniquePRs = $allResults | Select-Object -Property Repo,PRNumber -Unique
Write-Host "PRs WITH COPILOT INLINE COMMENTS: $($uniquePRs.Count)`n" -ForegroundColor White

# Per-repo breakdown
Write-Host "--- PER REPO BREAKDOWN ---" -ForegroundColor Cyan
foreach ($repoLabel in @("common", "msal", "broker")) {
    $repoComments = $allResults | Where-Object { $_.Repo -eq $repoLabel }
    $repoPRs = ($repoComments | Select-Object -Property PRNumber -Unique).Count
    $prsFile = "$env:TEMP\${repoLabel}_prs.json"
    $totalHumanPRs = ((Get-Content $prsFile | ConvertFrom-Json) | Where-Object { $_.author.login -notin $BOT_AUTHORS }).Count
    Write-Host "  $($repoLabel.ToUpper()): $($repoComments.Count) comments across $repoPRs PRs (out of $totalHumanPRs human PRs)"
}

# Classification breakdown
Write-Host "`n--- CLASSIFICATION BREAKDOWN ---" -ForegroundColor Cyan
$classifications = $allResults | Group-Object -Property Classification | Sort-Object Count -Descending
foreach ($c in $classifications) {
    $pct = if ($totalComments -gt 0) { [math]::Round(($c.Count / $totalComments) * 100, 1) } else { 0 }
    Write-Host "  $($c.Name): $($c.Count) ($pct%)"
}

# Helpful vs Unhelpful summary
$helpful = ($allResults | Where-Object { $_.Classification -eq "helpful-acknowledged" }).Count
$unhelpful = ($allResults | Where-Object { $_.Classification -eq "unhelpful-dismissed" }).Count
$noResponse = ($allResults | Where-Object { $_.Classification -eq "no-response" }).Count
$mixed = ($allResults | Where-Object { $_.Classification -eq "mixed-response" }).Count
$unclear = ($allResults | Where-Object { $_.Classification -eq "replied-unclear" }).Count

Write-Host "`n--- HELPFULNESS SUMMARY ---" -ForegroundColor Cyan
Write-Host "  Helpful (acknowledged/addressed):  $helpful" -ForegroundColor Green
Write-Host "  Unhelpful (dismissed/rejected):    $unhelpful" -ForegroundColor Red
Write-Host "  No response (ignored):             $noResponse" -ForegroundColor DarkGray
Write-Host "  Mixed response:                    $mixed" -ForegroundColor Yellow
Write-Host "  Replied but unclear sentiment:     $unclear" -ForegroundColor DarkYellow

$responded = $helpful + $unhelpful + $mixed + $unclear
if ($responded -gt 0) {
    $helpfulRate = [math]::Round(($helpful / $responded) * 100, 1)
    Write-Host "`n  Helpfulness rate (of responded): $helpfulRate%" -ForegroundColor White
}
if ($totalComments -gt 0) {
    $responseRate = [math]::Round(($responded / $totalComments) * 100, 1)
    Write-Host "  Response rate (any reply):        $responseRate%" -ForegroundColor White
    $overallHelpful = [math]::Round(($helpful / $totalComments) * 100, 1)
    Write-Host "  Overall helpfulness (of total):   $overallHelpful%" -ForegroundColor White
}

# Per-repo helpfulness
Write-Host "`n--- PER-REPO HELPFULNESS ---" -ForegroundColor Cyan
foreach ($repoLabel in @("common", "msal", "broker")) {
    $rc = $allResults | Where-Object { $_.Repo -eq $repoLabel }
    $rHelp = ($rc | Where-Object { $_.Classification -eq "helpful-acknowledged" }).Count
    $rUnhelp = ($rc | Where-Object { $_.Classification -eq "unhelpful-dismissed" }).Count
    $rNoResp = ($rc | Where-Object { $_.Classification -eq "no-response" }).Count
    $rMixed = ($rc | Where-Object { $_.Classification -eq "mixed-response" }).Count
    $rUnclear = ($rc | Where-Object { $_.Classification -eq "replied-unclear" }).Count
    $rTotal = $rc.Count
    $rResponded = $rHelp + $rUnhelp + $rMixed + $rUnclear
    $rRate = if ($rResponded -gt 0) { [math]::Round(($rHelp / $rResponded) * 100, 1) } else { "N/A" }
    Write-Host "  $($repoLabel.ToUpper()) ($rTotal comments): Helpful=$rHelp, Unhelpful=$rUnhelp, NoResponse=$rNoResp, Mixed=$rMixed, Unclear=$rUnclear | Helpfulness=$rRate%"
}

# Top commented files
Write-Host "`n--- TOP COMMENTED FILES ---" -ForegroundColor Cyan
$allResults | Group-Object -Property FilePath | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object {
    Write-Host "  $($_.Count)x  $($_.Name)"
}

# Per-author breakdown
Write-Host "`n--- COMMENTS RECEIVED PER PR AUTHOR ---" -ForegroundColor Cyan
$allResults | Group-Object -Property PRAuthor | Sort-Object Count -Descending | ForEach-Object {
    $authorComments = $_.Group
    $authorHelp = ($authorComments | Where-Object { $_.Classification -eq "helpful-acknowledged" }).Count
    $authorUnhelp = ($authorComments | Where-Object { $_.Classification -eq "unhelpful-dismissed" }).Count
    $authorNoResp = ($authorComments | Where-Object { $_.Classification -eq "no-response" }).Count
    $authorTotal = $_.Count
    Write-Host "  $($_.Name): $authorTotal total (helpful=$authorHelp, unhelpful=$authorUnhelp, no-response=$authorNoResp)"
}

# Sample comments per classification
foreach ($cls in @("helpful-acknowledged", "unhelpful-dismissed", "no-response", "replied-unclear")) {
    $clsComments = $allResults | Where-Object { $_.Classification -eq $cls }
    if ($clsComments.Count -gt 0) {
        $displayName = switch ($cls) {
            "helpful-acknowledged" { "HELPFUL COMMENTS" }
            "unhelpful-dismissed"  { "UNHELPFUL/DISMISSED COMMENTS" }
            "no-response"          { "IGNORED (NO RESPONSE) COMMENTS" }
            "replied-unclear"      { "UNCLEAR RESPONSE COMMENTS" }
        }
        $color = switch ($cls) {
            "helpful-acknowledged" { "Green" }
            "unhelpful-dismissed"  { "Red" }
            "no-response"          { "DarkGray" }
            "replied-unclear"      { "DarkYellow" }
        }
        Write-Host "`n--- SAMPLE: $displayName ---" -ForegroundColor $color
        $clsComments | Select-Object -First 3 | ForEach-Object {
            Write-Host "  PR #$($_.PRNumber) ($($_.Repo)) - $($_.FilePath)" -ForegroundColor White
            $excerpt = ($_.CommentBody -replace "`n"," " -replace "`r","")
            if ($excerpt.Length -gt 180) { $excerpt = $excerpt.Substring(0, 180) + "..." }
            Write-Host "    Copilot: $excerpt" -ForegroundColor $color
            if ($_.HumanReplyText.Length -gt 0) {
                $replyExcerpt = ($_.HumanReplyText -replace "`n"," " -replace "`r","")
                if ($replyExcerpt.Length -gt 120) { $replyExcerpt = $replyExcerpt.Substring(0, 120) + "..." }
                Write-Host "    Human:   $replyExcerpt" -ForegroundColor White
            }
            Write-Host ""
        }
    }
}

Write-Host "`n================================================================" -ForegroundColor Yellow
Write-Host "Analysis complete. Raw data: $OutputDir" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Yellow
