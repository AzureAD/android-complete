<#
.SYNOPSIS
    Collect all Copilot code review comments across 3 Android Auth repos.
    Records whether each comment received a human reply (and the reply text).
    Does NOT classify replies — that is done by the AI agent in Phase 3.
#>

param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$StartDate = (Get-Date).AddDays(-60).ToString("yyyy-MM-dd")
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ========================================
# AUTH: Switch to EMU account (has access to all repos including private broker)
# EMU accounts follow the *_microsoft naming convention.
# ========================================
$originalAccount = gh api user --jq '.login' 2>$null

# Find the EMU account from gh auth status output
$emuAccount = (gh auth status 2>&1 | Select-String 'Logged in to github.com account (\S+_microsoft)' | 
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Select-Object -First 1)

if (-not $emuAccount) {
    Write-Host "ERROR: No EMU account (*_microsoft) found in 'gh auth status'." -ForegroundColor Red
    Write-Host "  Run: gh auth login  (and authenticate with your EMU account)" -ForegroundColor Yellow
    exit 1
}

if ($originalAccount -ne $emuAccount) {
    Write-Host "Switching from '$originalAccount' to EMU account '$emuAccount'..." -ForegroundColor Cyan
    gh auth switch --user $emuAccount 2>&1 | Out-Null
    $currentAccount = gh api user --jq '.login' 2>$null
    if ($currentAccount -ne $emuAccount) {
        Write-Host "ERROR: Failed to switch to EMU account '$emuAccount'." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Switched to '$emuAccount'. Will restore '$originalAccount' on completion." -ForegroundColor Green
} else {
    Write-Host "Already using EMU account '$emuAccount'." -ForegroundColor Green
    $originalAccount = $null  # no restore needed
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
                
                $hasReply = $replies.Count -gt 0
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
                    HasReply         = $hasReply
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
Write-Host "           COPILOT CODE REVIEW DATA COLLECTION RESULTS" -ForegroundColor Yellow
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

# Reply rate
$replied = ($allResults | Where-Object { $_.HasReply -eq $true }).Count
$noReply = ($allResults | Where-Object { $_.HasReply -eq $false }).Count
Write-Host "`n--- REPLY BREAKDOWN ---" -ForegroundColor Cyan
Write-Host "  Replied:    $replied ($([math]::Round($replied/$totalComments*100,1))%)" -ForegroundColor Green
Write-Host "  No reply:   $noReply ($([math]::Round($noReply/$totalComments*100,1))%)" -ForegroundColor DarkGray

# Top commented files
Write-Host "`n--- TOP COMMENTED FILES ---" -ForegroundColor Cyan
$allResults | Group-Object -Property FilePath | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object {
    Write-Host "  $($_.Count)x  $($_.Name)"
}

# Per-author breakdown
Write-Host "`n--- COMMENTS RECEIVED PER PR AUTHOR ---" -ForegroundColor Cyan
$allResults | Group-Object -Property PRAuthor | Sort-Object Count -Descending | ForEach-Object {
    $authorComments = $_.Group
    $authorReplied = ($authorComments | Where-Object { $_.HasReply -eq $true }).Count
    $authorTotal = $_.Count
    Write-Host "  $($_.Name): $authorTotal total (replied=$authorReplied, no-reply=$($authorTotal - $authorReplied))"
}

Write-Host "`n================================================================"
Write-Host "Data saved to: $OutputDir"
Write-Host "  raw_results.json ($totalComments comments)"
Write-Host "  review_summaries.json ($($reviewSummaries.Count) summaries)"
Write-Host "================================================================"
Write-Host "`nNext: Run Phase 2 (precise.ps1) for diff verification," -ForegroundColor Yellow
Write-Host "then Phase 3 (AI classification of all replied comments)." -ForegroundColor Yellow

# ========================================
# RESTORE: Switch back to original GitHub account
# ========================================
if ($originalAccount) {
    Write-Host "`nRestoring GitHub CLI to original account '$originalAccount'..." -ForegroundColor Cyan
    gh auth switch --user $originalAccount 2>&1 | Out-Null
    Write-Host "  Restored to '$originalAccount'." -ForegroundColor Green
}
