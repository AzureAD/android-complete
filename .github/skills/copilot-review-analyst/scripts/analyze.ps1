<#
.SYNOPSIS
    Collect all Copilot code review comments across 3 Android Auth repos.
    Records whether each comment received a human reply (and the reply text).
    Does NOT classify replies — that is done by the AI agent in Phase 3.
#>

param(
    [string]$OutputDir = "$env:TEMP\copilot-review-analysis",
    [string]$StartDate = (Get-Date).AddDays(-60).ToString("yyyy-MM-dd"),
    [string]$EndDate = (Get-Date).ToString("yyyy-MM-dd")
)

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ========================================
# AUTH: common/msal live on github.com; broker lives on msft.ghe.com.
# Both hosts must be authenticated — gh routes per-host, so no account switching.
# ========================================
$originalAccount = $null

if (-not (gh auth status --hostname github.com 2>&1 | Select-String 'Logged in')) {
    Write-Host "ERROR: not logged in to github.com." -ForegroundColor Red
    Write-Host "  Run: gh auth login --hostname github.com" -ForegroundColor Yellow
    exit 1
}

if (-not (gh auth status --hostname msft.ghe.com 2>&1 | Select-String 'Logged in')) {
    Write-Host "ERROR: not logged in to msft.ghe.com (required for the broker repo)." -ForegroundColor Red
    Write-Host "  Run: gh auth login --hostname msft.ghe.com" -ForegroundColor Yellow
    exit 1
}

# Copilot uses "Copilot" for inline review comments
$COPILOT_USERS = @("Copilot", "copilot-pull-request-reviewer[bot]")
$BOT_AUTHORS = @("app/copilot-swe-agent", "Copilot", "dependabot[bot]", "github-actions[bot]")

$repos = @(
    @{ Label = "common"; Slug = "AzureAD/microsoft-authentication-library-common-for-android"; PrRepo = "AzureAD/microsoft-authentication-library-common-for-android"; ApiRepo = "https://api.github.com/repos/AzureAD/microsoft-authentication-library-common-for-android" },
    @{ Label = "msal";   Slug = "AzureAD/microsoft-authentication-library-for-android";        PrRepo = "AzureAD/microsoft-authentication-library-for-android";        ApiRepo = "https://api.github.com/repos/AzureAD/microsoft-authentication-library-for-android" },
    @{ Label = "broker"; Slug = "security/ad-accounts-for-android";                            PrRepo = "msft.ghe.com/security/ad-accounts-for-android";                ApiRepo = "https://msft.ghe.com/api/v3/repos/security/ad-accounts-for-android" }
)

# ========================================
# Force-push / stale-snapshot detection
# A review comment's `original_commit_id` is the immutable commit Copilot reviewed.
# If that SHA is NOT in the PR's current commit list, the reviewed snapshot was
# rewritten (rebase/force-push) and GitHub will mark the comment "outdated" even
# when Copilot was correct at the moment it reviewed. We flag this so a correct
# comment is never scored as a false positive. See references/classification-rules.md
# ("The Force-Push Confound"). Commit SHA lists are fetched once per PR and cached.
# ========================================
$prCommitShaCache = @{}
function Get-PRCommitShas($apiRepo, $prNum) {
    $key = "$apiRepo/$prNum"
    if (-not $prCommitShaCache.ContainsKey($key)) {
        try {
            $shas = gh api "$apiRepo/pulls/$prNum/commits" --paginate --jq '.[].sha' 2>&1
            $prCommitShaCache[$key] = @($shas | Where-Object { $_ -match '^[0-9a-f]{40}$' })
        } catch {
            $prCommitShaCache[$key] = @()
        }
    }
    return $prCommitShaCache[$key]
}

# ========================================
# Apply-suggestion / silent-adoption detection (Tier 1.3, borrowed from Apple/JS)
# An engineer can adopt a Copilot comment WITHOUT replying — via GitHub's native
# "Apply suggestion" button (commit message "Apply suggestions from code review",
# co-authored by Copilot), a Copilot Autofix commit ("Potential fix for pull request
# finding"), or a Copilot coding-agent commit ("Co-authored-by: Copilot ..."). Such a
# comment gets scored "unresolved" (no reply, diff-verify may miss a relocated change)
# even though it was helpful. We fingerprint, per PR, every "apply-ish" commit dated
# AFTER a comment that touches the comment's file, and tag the comment as an
# ApplyCandidate with a confidence tier:
#   native-apply  -> HIGH   (the button literally applies the suggestion verbatim)
#   autofix       -> MEDIUM (Copilot-generated fix; usually but not always the comment)
#   coauthor      -> LOW    (coding-agent touched the file; may be unrelated)
# The collector only TAGS. Promotion unresolved->helpful is decided in Phase 4 as a
# strict fallback: auto-credit native-apply; route autofix/coauthor for confirmation.
# Never let this DEMOTE a comment. Commits are fetched once per PR and cached.
# ========================================
$prApplyCache = @{}
function Get-PRApplyCommits($apiRepo, $prNum) {
    $key = "$apiRepo/$prNum"
    if (-not $prApplyCache.ContainsKey($key)) {
        $out = @()
        try {
            $commits = gh api "$apiRepo/pulls/$prNum/commits" --paginate 2>&1 | ConvertFrom-Json
            foreach ($cmt in $commits) {
                $msg = $cmt.commit.message
                $tier = $null
                if ($msg -match 'Apply suggestions? from code review') { $tier = 'native-apply' }
                elseif ($msg -match 'Potential fix for pull request finding') { $tier = 'autofix' }
                elseif ($msg -match 'Co-authored-by:\s*Copilot') { $tier = 'coauthor' }
                if ($tier) {
                    $files = @()
                    try {
                        $det = gh api "$apiRepo/commits/$($cmt.sha)" 2>$null | ConvertFrom-Json
                        $files = @($det.files | ForEach-Object { $_.filename })
                    } catch {}
                    $out += [PSCustomObject]@{
                        Sha   = $cmt.sha
                        Date  = $cmt.commit.author.date
                        Tier  = $tier
                        Files = $files
                    }
                }
            }
        } catch {}
        $prApplyCache[$key] = $out
    }
    return $prApplyCache[$key]
}

# Tracks, per repo, which PR numbers Copilot posted ANY review on (for coverage.json)
$reviewedPRsByRepo = @{}

$allResults = @()
$reviewSummaries = @()

foreach ($repo in $repos) {
    $label = $repo.Label
    $slug = $repo.Slug
    $apiRepo = $repo.ApiRepo
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "Processing: $label ($slug)" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    # Load cached PR list.
    # MERGED-ONLY WINDOW (Apple parity): score only PRs actually MERGED in the window.
    # Using `--state all` + `created:>=` (the old behaviour) mixed in still-open / abandoned
    # PRs and PRs created-but-not-merged, inflating denominators and letting a comment be
    # judged against code that never shipped. `merged:START..END` is the correct basis.
    $prsFile = "$env:TEMP\${label}_prs.json"
    if (-not (Test-Path $prsFile)) {
        Write-Host "  Fetching MERGED PR list ($StartDate..$EndDate)..."
        gh pr list --repo $repo.PrRepo --state merged --limit 300 --json number,title,author,createdAt,mergedAt,state --search "merged:$StartDate..$EndDate" 2>&1 | Out-File -FilePath $prsFile -Encoding utf8
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
            $commentsRaw = gh api "$apiRepo/pulls/$prNum/comments" --paginate 2>&1
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

                # Force-push / stale-snapshot fingerprint: is the commit Copilot
                # reviewed (original_commit_id) still in the PR's commit list?
                $origCommit = $cc.original_commit_id
                $diffHunk = $cc.diff_hunk
                $prShas = Get-PRCommitShas $apiRepo $prNum
                $reviewedRewritten = $false
                if ($origCommit -and $prShas.Count -gt 0) {
                    $reviewedRewritten = -not ($prShas -contains $origCommit)
                }

                # Reaction signal (Tier 1.3): only inspect when the comment payload's
                # reactions summary shows activity, so we never spend an API call on the
                # ~100% of comments with zero reactions. Reactions do NOT count toward the
                # response rate; they are only a fallback adoption/rejection hint.
                $thumbsUp = 0; $thumbsDown = 0
                if ($cc.reactions -and $cc.reactions.total_count -gt 0) {
                    $thumbsUp = [int]$cc.reactions.'+1'
                    $thumbsDown = [int]$cc.reactions.'-1'
                }

                # Apply-suggestion signal (Tier 1.3): did an apply-ish commit dated after
                # this comment touch this comment's file? Record the highest-confidence tier.
                $applyCandidate = $false; $applyTier = $null; $applySha = $null
                $ccCreated = $null
                try { $ccCreated = [datetime]$cc.created_at } catch {}
                if ($ccCreated) {
                    $tierRank = @{ 'native-apply' = 3; 'autofix' = 2; 'coauthor' = 1 }
                    $best = 0
                    foreach ($ac in (Get-PRApplyCommits $apiRepo $prNum)) {
                        $acDate = $null; try { $acDate = [datetime]$ac.Date } catch {}
                        if ($acDate -and $acDate -gt $ccCreated -and ($ac.Files -contains $commentPath)) {
                            $applyCandidate = $true
                            if ($tierRank[$ac.Tier] -gt $best) { $best = $tierRank[$ac.Tier]; $applyTier = $ac.Tier; $applySha = $ac.Sha }
                        }
                    }
                }

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
                    OriginalCommitId = $origCommit
                    DiffHunk         = if ($diffHunk -and $diffHunk.Length -gt 2000) { $diffHunk.Substring(0, 2000) + "..." } else { $diffHunk }
                    ReviewedCommitRewritten = $reviewedRewritten
                    ThumbsUp         = $thumbsUp
                    ThumbsDown       = $thumbsDown
                    ApplyCandidate   = $applyCandidate
                    ApplyTier        = $applyTier
                    ApplyCommitSha   = $applySha
                }
            }
        }
        
        # Also check the review-level summary comments from copilot
        try {
            $reviewsRaw = gh api "$apiRepo/pulls/$prNum/reviews" 2>&1
            $reviews = $reviewsRaw | ConvertFrom-Json
            # Coverage: a PR counts as "reviewed by Copilot" if Copilot posted ANY review
            # (even a body-less APPROVED/COMMENTED), not only ones with a summary body.
            $anyCopilotReview = $reviews | Where-Object { $_.user.login -in $COPILOT_USERS }
            if ($anyCopilotReview) {
                if (-not $reviewedPRsByRepo.ContainsKey($label)) { $reviewedPRsByRepo[$label] = @{} }
                $reviewedPRsByRepo[$label][[string]$prNum] = $true
            }
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
# COVERAGE (Tier 2.4): reviewed vs with-feedback vs no-feedback, per repo + overall.
# "reviewed"      = merged human PRs where Copilot posted any review.
# "with-feedback" = of those, PRs where Copilot left >=1 inline comment.
# "no-feedback"   = reviewed but zero inline comments (Copilot approved/looked, said nothing).
# This surfaces the silent-zero-repo failure (e.g. an EMU token that can't see broker)
# and frames the whole report: helpfulness is measured only on the with-feedback slice.
# ========================================
$coverage = @{ perRepo = @(); overall = @{} }
$ovMerged = 0; $ovReviewed = 0; $ovFeedback = 0
foreach ($repoLabel in @("common", "msal", "broker")) {
    $prsFile = "$env:TEMP\${repoLabel}_prs.json"
    $mergedHumanPRs = 0
    if (Test-Path $prsFile) {
        $mergedHumanPRs = @((Get-Content $prsFile | ConvertFrom-Json) | Where-Object { $_.author.login -notin $BOT_AUTHORS }).Count
    }
    $reviewedCount = if ($reviewedPRsByRepo.ContainsKey($repoLabel)) { $reviewedPRsByRepo[$repoLabel].Keys.Count } else { 0 }
    $feedbackPRs = @($allResults | Where-Object { $_.Repo -eq $repoLabel } | Select-Object -ExpandProperty PRNumber -Unique).Count
    $coverage.perRepo += [PSCustomObject]@{
        repo = $repoLabel
        mergedHumanPRs = $mergedHumanPRs
        reviewedByCopilot = $reviewedCount
        withInlineFeedback = $feedbackPRs
        noFeedback = [Math]::Max(0, $reviewedCount - $feedbackPRs)
        reviewCoveragePct = if ($mergedHumanPRs -gt 0) { [math]::Round($reviewedCount / $mergedHumanPRs * 100, 1) } else { 0 }
    }
    $ovMerged += $mergedHumanPRs; $ovReviewed += $reviewedCount; $ovFeedback += $feedbackPRs
}
$coverage.overall = [PSCustomObject]@{
    mergedHumanPRs = $ovMerged
    reviewedByCopilot = $ovReviewed
    withInlineFeedback = $ovFeedback
    noFeedback = [Math]::Max(0, $ovReviewed - $ovFeedback)
    reviewCoveragePct = if ($ovMerged -gt 0) { [math]::Round($ovReviewed / $ovMerged * 100, 1) } else { 0 }
}
$coverage | ConvertTo-Json -Depth 5 | Out-File "$OutputDir\coverage.json" -Encoding utf8

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

# Force-push / stale-snapshot fingerprint (reviewed commit rewritten away)
$rewritten = ($allResults | Where-Object { $_.ReviewedCommitRewritten -eq $true }).Count
if ($rewritten -gt 0) {
    Write-Host "`n--- FORCE-PUSH / STALE-SNAPSHOT FINGERPRINT ---" -ForegroundColor Cyan
    Write-Host "  $rewritten comment(s) reviewed against a since-rewritten commit." -ForegroundColor DarkYellow
    Write-Host "  Any 'outdated / already there / not relevant' dismissal on these MUST be" -ForegroundColor DarkYellow
    Write-Host "  validated against the immutable diff_hunk before scoring (see classification-rules.md)." -ForegroundColor DarkYellow
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
