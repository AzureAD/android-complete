// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
    currentStatusOwners,
    currentStatuses,
    renderCurrentStatusBlock,
    statusMarkers,
    upsertMarkerBlock,
} from "./status.mjs";
import {
    BUILDER_AGENT,
    REVIEWER_AGENT,
    discoverChangedFilesFromGit,
    ensureIsolatedTargetWorktree,
    pauseForHelp,
    resolveGitHubAccount,
    resolveRoleSkillGuides,
    revertUnauthorizedProtectedFiles,
    runLocalVerificationGate,
    slimResultSchema,
    slimReviewSchema,
    snapshotWorktree,
} from "./workflow.mjs";

function loadObserverAgentPrompt(agentName, fallbackPrompt = "") {
    try {
        const currentDir = path.dirname(fileURLToPath(import.meta.url));
        const agentFile = path.resolve(currentDir, "..", "..", "agents", `${agentName}.agent.md`);
        if (existsSync(agentFile)) {
            const raw = readFileSync(agentFile, "utf8").replace(/\r\n/g, "\n");
            const stripped = raw.replace(/^---\n[\s\S]*?\n---\n+/, "").trim();
            if (stripped.length > 0) return stripped;
        }
    } catch {
        // Fall back to inline prompt.
    }
    return fallbackPrompt;
}

export const OBSERVER_AGENT = "pr-observer";
export const MAX_OBSERVER_ITERATIONS = 2;
export const DEFAULT_CI_POLL_INTERVAL_MS = 60_000;
export const DEFAULT_MAX_CI_POLL_ATTEMPTS = 60;
export const MAX_JS_POLLS_BEFORE_HEARTBEAT = 7;

const text = { type: "string" };
const texts = { type: "array", items: text };
const object = (properties) => ({
    type: "object",
    properties,
    required: Object.keys(properties),
});

const nonempty = (value) => typeof value === "string" && value.trim().length > 0;
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const positiveInt = (value) => Number.isSafeInteger(value) && value > 0;

export const observationSchema = object({
    verdict: {
        type: "string",
        enum: ["ready", "repair", "waiting-for-ci", "needs-owner-decision"],
    },
    repairKind: {
        type: "string",
        enum: ["none", "ci-failure", "review-comment", "merge-conflict"],
    },
    summary: text,
    evidence: texts,
    actionableFindings: texts,
});

export const observerCustomAgent = Object.freeze({
    name: OBSERVER_AGENT,
    displayName: "Autonomous PR Observer",
    description: "Monitors an Android Auth pull request's CI pipelines and review threads, classifying tactical repairs vs. ready-for-human-merge.",
    infer: false,
    tools: ["view", "glob", "grep", "skill"],
    prompt: loadObserverAgentPrompt(
        OBSERVER_AGENT,
        `You are the read-only Autonomous PR Observer for Android Auth pull requests.
Inspect the supplied pull request snapshot (CI check statuses, mergeability, and review comments/threads) and repository context.
Classify the PR into one verdict:
- "ready": All required CI checks have passed, there are no merge conflicts, and there are no unresolved actionable review findings.
- "repair": There is a concrete tactical fix needed (a CI build/test failure such as a Kotlin/Java compile error or unit test failure, an actionable inline review comment, or a merge conflict). List each required fix in actionableFindings.
- "waiting-for-ci": Checks are still queued/in-progress and there are no actionable failures or review findings yet.
- "needs-owner-decision": A reviewer requested a breaking architectural or product-scope change that requires human owner confirmation.
Never merge or approve a PR on a human's behalf.`,
    ),
});

function defaultExec(command, args, options = {}) {
    return execFileSync(command, args, {
        cwd: options.cwd,
        env: options.env ? { ...process.env, ...options.env } : process.env,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
    }).trim();
}

const ALLOWED_GITHUB_PR_REPOS = Object.freeze({
    "msft.ghe.com/security/ad-accounts-for-android": Object.freeze({
        hostname: "msft.ghe.com",
        slug: "security/ad-accounts-for-android",
        repository: "broker",
        localPath: "broker",
    }),
    "github.com/azuread/microsoft-authentication-library-common-for-android": Object.freeze({
        hostname: "github.com",
        slug: "AzureAD/microsoft-authentication-library-common-for-android",
        repository: "common",
        localPath: "common",
    }),
    "github.com/azuread/microsoft-authentication-library-for-android": Object.freeze({
        hostname: "github.com",
        slug: "AzureAD/microsoft-authentication-library-for-android",
        repository: "msal",
        localPath: "msal",
    }),
    "github.com/azuread/azure-activedirectory-library-for-android": Object.freeze({
        hostname: "github.com",
        slug: "AzureAD/azure-activedirectory-library-for-android",
        repository: "adal",
        localPath: "adal",
    }),
    "github.com/azuread/android-complete": Object.freeze({
        hostname: "github.com",
        slug: "AzureAD/android-complete",
        repository: "android-complete",
        localPath: ".",
    }),
});

const ALLOWED_ADO_PR_REPOS = Object.freeze({
    "msazure/one/ad-mfa-phonefactor-phoneapp-android": Object.freeze({
        org: "msazure",
        project: "One",
        repoName: "AD-MFA-phonefactor-phoneApp-android",
        repository: "authenticator",
        localPath: "authenticator",
    }),
});

export function parsePullRequestUrl(prUrl) {
    if (!nonempty(prUrl)) return null;
    const trimmed = prUrl.trim();
    const adoMatch = trimmed.match(
        /^https:\/\/(?:dev\.azure\.com\/([^/\s]+)\/([^/\s]+)|([^.\s]+)\.visualstudio\.com\/(?:DefaultCollection\/)?([^/\s]+))\/_git\/([^/\s?]+)\/pullrequest\/([1-9]\d*)(?:[/?#].*)?$/i,
    );
    if (adoMatch) {
        const rawOrg = adoMatch[1] || adoMatch[3];
        const rawProject = decodeURIComponent(adoMatch[2] || adoMatch[4]);
        const rawRepoName = decodeURIComponent(adoMatch[5]);
        const prNumber = Number(adoMatch[6]);
        const adoKey = `${rawOrg.toLowerCase()}/${rawProject.toLowerCase()}/${rawRepoName.toLowerCase()}`;
        const allowedAdo = ALLOWED_ADO_PR_REPOS[adoKey];
        if (!allowedAdo) return null;
        const hostname = adoMatch[1] ? "dev.azure.com" : `${allowedAdo.org}.visualstudio.com`;
        const orgUrl = adoMatch[1] ? `https://dev.azure.com/${allowedAdo.org}` : `https://${allowedAdo.org}.visualstudio.com`;
        return {
            provider: "ado",
            prUrl: `${orgUrl}/${allowedAdo.project}/_git/${allowedAdo.repoName}/pullrequest/${prNumber}`,
            hostname,
            org: allowedAdo.org,
            orgUrl,
            project: allowedAdo.project,
            repoName: allowedAdo.repoName,
            slug: `${allowedAdo.project}/_git/${allowedAdo.repoName}`,
            prNumber,
            repository: allowedAdo.repository,
            localPath: allowedAdo.localPath,
        };
    }

    const match = trimmed.match(/^https:\/\/([^/]+)\/([^/]+\/[^/]+)\/pull\/([1-9]\d*)(?:\/.*)?$/i);
    if (!match) return null;
    const [, rawHostname, rawSlug, numStr] = match;
    const ghKey = `${rawHostname.toLowerCase()}/${rawSlug.toLowerCase()}`;
    const allowedGh = ALLOWED_GITHUB_PR_REPOS[ghKey];
    if (!allowedGh) return null;
    return {
        provider: "github",
        prUrl: `https://${allowedGh.hostname}/${allowedGh.slug}/pull/${numStr}`,
        hostname: allowedGh.hostname,
        slug: allowedGh.slug,
        prNumber: Number(numStr),
        repository: allowedGh.repository,
        localPath: allowedGh.localPath,
    };
}

export function verifyCheckoutRemoteMatchesPr(repoRoot, parsedPr, exec = defaultExec) {
    if (!record(parsedPr) || !nonempty(repoRoot)) {
        return { ok: false, reason: "Missing pull request target or repository root." };
    }
    const hasDotGit = existsSync(path.join(repoRoot, ".git"));
    let remoteUrl = "";
    try {
        remoteUrl = exec("git", ["remote", "get-url", "origin"], { cwd: repoRoot });
    } catch {
        remoteUrl = "";
    }
    if (!nonempty(remoteUrl)) {
        if (exec === defaultExec && hasDotGit) {
            return {
                ok: false,
                reason: `Could not verify origin remote URL in ${repoRoot} for ${parsedPr.prUrl}.`,
            };
        }
        return { ok: true, remoteUrl: "" };
    }
    const normalizedRemote = remoteUrl.trim().replace(/\.git$/i, "").replace(/\/+$/, "").toLowerCase();
    if (parsedPr.provider === "github") {
        const expectedHost = String(parsedPr.hostname || "").toLowerCase();
        const expectedSlug = String(parsedPr.slug || "").toLowerCase();
        const httpsMarker = `${expectedHost}/${expectedSlug}`;
        const sshMarker = `${expectedHost}:${expectedSlug}`;
        if (!normalizedRemote.includes(httpsMarker) && !normalizedRemote.includes(sshMarker)) {
            return {
                ok: false,
                reason: `Checkout origin remote (${remoteUrl.trim()}) does not match expected GitHub repository ${parsedPr.hostname}/${parsedPr.slug}.`,
            };
        }
        return { ok: true, remoteUrl: remoteUrl.trim() };
    }
    if (parsedPr.provider === "ado") {
        const expectedOrg = String(parsedPr.org || "").toLowerCase();
        const expectedRepo = String(parsedPr.repoName || "").toLowerCase();
        if (!normalizedRemote.includes(expectedOrg) || !normalizedRemote.includes(`/_git/${expectedRepo}`)) {
            return {
                ok: false,
                reason: `Checkout origin remote (${remoteUrl.trim()}) does not match expected Azure DevOps repository ${parsedPr.org}/${parsedPr.project}/_git/${parsedPr.repoName}.`,
            };
        }
        return { ok: true, remoteUrl: remoteUrl.trim() };
    }
    return { ok: false, reason: `Unsupported pull request provider: ${parsedPr.provider}` };
}

function normalizeThreadRepoPath(value) {
    return String(value || "").trim().replace(/\\/g, "/").replace(/^\/+/, "");
}

function pathsReferToSameFile(a, b) {
    const normA = normalizeThreadRepoPath(a).toLowerCase();
    const normB = normalizeThreadRepoPath(b).toLowerCase();
    if (!normA || !normB) return false;
    return normA === normB || normA.endsWith(`/${normB}`) || normB.endsWith(`/${normA}`);
}

export function selectAddressedReviewThreads({
    threads = [],
    observation = null,
    repairResult = null,
    changedFiles = [],
} = {}) {
    if (!Array.isArray(threads) || threads.length === 0) return [];
    const findingsList = Array.isArray(observation?.actionableFindings)
        ? observation.actionableFindings.map((f) => String(f || "").trim()).filter(Boolean)
        : [];
    const findingsLower = findingsList.join("\n").toLowerCase();
    const repairEvidenceList = Array.isArray(repairResult?.evidence)
        ? repairResult.evidence.map((e) => String(e || "").trim()).filter(Boolean)
        : [];
    const repairLower = [repairResult?.summary || "", ...repairEvidenceList].join("\n").toLowerCase();
    const combinedLower = `${findingsLower}\n${repairLower}`;

    const normalizedChanged = (Array.isArray(changedFiles) ? changedFiles : [])
        .map(normalizeThreadRepoPath)
        .filter(Boolean);
    const normalizedEvidencePaths = repairEvidenceList
        .map((entry) => normalizeThreadRepoPath(entry.split(":")[0]))
        .filter(Boolean);
    const confirmedFiles = normalizedChanged.length > 0 ? normalizedChanged : normalizedEvidencePaths;

    const inlineThreads = threads.filter((t) => nonempty(normalizeThreadRepoPath(t?.filePath || t?.path)));
    const pathCounts = new Map();
    for (const t of inlineThreads) {
        const p = normalizeThreadRepoPath(t?.filePath || t?.path).toLowerCase();
        pathCounts.set(p, (pathCounts.get(p) || 0) + 1);
    }

    const isInlineThreadAddressed = (thread) => {
        const numericId = positiveInt(Number(thread?.id)) ? String(Number(thread.id)) : "";
        const graphqlId = nonempty(thread?.threadId) ? String(thread.threadId).trim().toLowerCase() : "";
        if (graphqlId && findingsLower.includes(graphqlId) && (normalizedChanged.length > 0 || repairLower.includes(graphqlId))) {
            return true;
        }
        if (numericId) {
            const idRegex = new RegExp(`\\b(?:thread|comment|id|#)\\s*${numericId}\\b`, "i");
            if (idRegex.test(findingsLower) && (normalizedChanged.length > 0 || idRegex.test(repairLower))) {
                return true;
            }
        }

        const threadPath = normalizeThreadRepoPath(thread?.filePath || thread?.path);
        if (!nonempty(threadPath)) return false;

        const fileConfirmedByRepair = confirmedFiles.some((f) => pathsReferToSameFile(f, threadPath));
        if (!fileConfirmedByRepair) return false;

        const baseName = path.posix.basename(threadPath).toLowerCase();
        const threadBody = nonempty(thread?.body)
            ? String(thread.body)
            : Array.isArray(thread?.comments)
                ? thread.comments.map((c) => String(c?.content || "")).join("\n")
                : "";
        const backtickTerms = [...threadBody.matchAll(/`([A-Za-z_][A-Za-z0-9_().]{2,})`/g)]
            .map((m) => m[1].replace(/\(\)$/, "").toLowerCase())
            .filter((t) => t.length >= 3);
        const lineStr = positiveInt(Number(thread?.line)) ? String(Number(thread.line)) : "";

        const findingMentionsFile = findingsLower.includes(threadPath.toLowerCase()) || findingsLower.includes(baseName);
        const findingMentionsTerm = backtickTerms.some((term) => findingsLower.includes(term));
        if (!findingMentionsFile && !findingMentionsTerm) return false;

        const sameFileThreadCount = pathCounts.get(threadPath.toLowerCase()) || 1;
        if (sameFileThreadCount > 1) {
            const hasLineMatch = nonempty(lineStr) && new RegExp(`(?::|\\bline\\s+)${lineStr}\\b`, "i").test(combinedLower);
            const hasTermMatch = backtickTerms.some((term) => combinedLower.includes(term));
            if (!hasLineMatch && !hasTermMatch) {
                return false;
            }
        }
        return true;
    };

    const addressedInline = new Set();
    for (const thread of inlineThreads) {
        if (isInlineThreadAddressed(thread)) {
            addressedInline.add(thread);
        }
    }

    return threads.filter((thread) => {
        const threadPath = normalizeThreadRepoPath(thread?.filePath || thread?.path);
        if (nonempty(threadPath)) {
            return addressedInline.has(thread);
        }
        const numericId = positiveInt(Number(thread?.id)) ? String(Number(thread.id)) : "";
        const graphqlId = nonempty(thread?.threadId) ? String(thread.threadId).trim().toLowerCase() : "";
        if (graphqlId && findingsLower.includes(graphqlId)) return true;
        if (numericId && new RegExp(`\\b(?:thread|comment|id|#)\\s*${numericId}\\b`, "i").test(findingsLower)) return true;

        const threadBody = nonempty(thread?.body)
            ? String(thread.body)
            : Array.isArray(thread?.comments)
                ? thread.comments.map((c) => String(c?.content || "")).join("\n")
                : "";
        if (
            /AuthApp PR Reviewer/i.test(threadBody) &&
            inlineThreads.length > 0 &&
            addressedInline.size === inlineThreads.length
        ) {
            return true;
        }
        return false;
    });
}

export function fetchPullRequestSnapshot(parsedPr, { workspaceRoot = process.cwd(), exec = defaultExec } = {}) {
    if (parsedPr?.provider === "ado") {
        const orgUrl = (parsedPr.orgUrl || `https://${parsedPr.hostname}`).replace(/\/+$/, "").replace(/'/g, "''");
        const projectEnc = encodeURIComponent(parsedPr.project);
        const repoEnc = encodeURIComponent(parsedPr.repoName);
        const psScript = [
            "$ErrorActionPreference = 'Stop'",
            "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
            "$headers = @{ Authorization = \"Bearer $token\" }",
            `$projectBase = '${orgUrl}/${projectEnc}'`,
            `$base = "$projectBase/_apis/git/repositories/${repoEnc}/pullRequests/${parsedPr.prNumber}"`,
            "$pr = Invoke-RestMethod -Uri ($base + '?api-version=7.0') -Headers $headers -Method Get",
            "$threads = (Invoke-RestMethod -Uri ($base + '/threads?api-version=7.0') -Headers $headers -Method Get).value",
            "$statuses = (Invoke-RestMethod -Uri ($base + '/statuses?api-version=7.0') -Headers $headers -Method Get).value",
            `$builds = (Invoke-RestMethod -Uri ("$projectBase/_apis/build/builds?branchName=refs/pull/${parsedPr.prNumber}/merge&api-version=7.0") -Headers $headers -Method Get).value`,
            "$activeThreads = @($threads | Where-Object { -not $_.isDeleted -and ($_.status -eq 'active' -or $_.status -eq 1) } | ForEach-Object { @{ id = $_.id; status = $_.status; filePath = $_.threadContext.filePath; comments = @($_.comments | ForEach-Object { @{ id = $_.id; author = $_.author.displayName; content = $_.content } }) } })",
            "$maxIter = ($statuses | Where-Object { $_.iterationId } | Measure-Object -Property iterationId -Maximum).Maximum",
            "$latestStatuses = @($statuses | Where-Object { -not $_.iterationId -or -not $maxIter -or $_.iterationId -eq $maxIter } | Group-Object { \"$($_.context.genre)/$($_.context.name)\" } | ForEach-Object { $_.Group | Sort-Object id -Descending | Select-Object -First 1 | Select-Object state, description, context })",
            "$latestBuilds = @($builds | Group-Object { $_.definition.name } | ForEach-Object { $b = $_.Group | Sort-Object id -Descending | Select-Object -First 1; $failedTasks = @(); $needsPolicyRequeue = $false; $hasInfraFailure = $false; if ($b.result -eq 'failed' -or $b.status -eq 'inProgress') { try { $tl = Invoke-RestMethod -Uri (\"$projectBase/_apis/build/builds/$($b.id)/timeline?api-version=7.0\") -Headers $headers -Method Get; if ($b.status -eq 'inProgress') { $hungEmu = @($tl.records | Where-Object { $_.type -eq 'Task' -and $_.name -eq 'Start emulator' -and $_.state -eq 'inProgress' -and $_.startTime -and (([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse([string]$_.startTime)).TotalMinutes -gt 45) }); if ($hungEmu.Count -gt 0) { $needsPolicyRequeue = $true } }; $rawFailed = @($tl.records | Where-Object { $_.type -eq 'Task' -and $_.result -eq 'failed' }); $hasPrimaryFailure = @($rawFailed | Where-Object { $_.name -notmatch 'Publish .* report' }).Count -gt 0; foreach ($rec in $rawFailed) { if ($hasPrimaryFailure -and $rec.name -match 'Publish .* report') { continue }; $taskIsInfra = $false; $issues = @($rec.issues | ForEach-Object { $_.message }); $excerpt = @(); $failedTests = @(); if ($rec.log.url) { try { $logText = Invoke-RestMethod -Uri ($rec.log.url + '?api-version=7.0') -Headers @{ Authorization = $headers.Authorization; Accept = 'text/plain' } -Method Get; if ($logText -isnot [string] -and $logText.value) { $logText = ($logText.value -join \"`n\") }; if ($logText -notmatch 'BUILD FAILED' -and $rec.parentId) { $parentRec = $tl.records | Where-Object { $_.id -eq $rec.parentId } | Select-Object -First 1; if ($parentRec -and $parentRec.log.url) { try { $parentTail = Invoke-RestMethod -Uri ($parentRec.log.url + '?startLine=65000&api-version=7.0') -Headers @{ Authorization = $headers.Authorization; Accept = 'text/plain' } -Method Get; if ($parentTail -isnot [string] -and $parentTail.value) { $parentTail = ($parentTail.value -join \"`n\") }; if ($parentTail) { $logText = $logText + \"`n\" + $parentTail } } catch {} } }; $excerpt = @($logText -split \"`r?`n\" | Where-Object { $_ -notmatch '\\tat org\\.gradle\\.|\\tat java\\.base/|\\sw:\\s' -and $_ -cmatch 'FAILED|What went wrong|\\.kt:\\d+|\\.java:\\d+|Execution failed|weighted issues|Caused by:|error:|fetch-pack:' } | Select-Object -Last 25); $runIds = @([regex]::Matches($logText, 'run id:\\s*(\\d+)') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique); foreach ($rid in $runIds) { try { $tr = (Invoke-RestMethod -Uri (\"$projectBase/_apis/test/Runs/$rid/results?outcomes=Failed&api-version=7.0\") -Headers $headers -Method Get).value; foreach ($tc in @($tr | Select-Object -First 20)) { $failedTests += @{ testClass = $tc.automatedTestStorage; testName = $tc.automatedTestName; errorMessage = $tc.errorMessage; stackTrace = @(($tc.stackTrace -split \"`r?`n\") | Select-Object -First 8) } } } catch {} } } catch {} }; if ($failedTests.Count -eq 0 -and (($issues + $excerpt) -join \"`n\") -match 'UnknownHostException|nodename nor servname|502 Bad Gateway|503 Service Unavailable|ETIMEDOUT|ECONNRESET|Connection reset by peer|unexpected disconnect while reading sideband packet|Git fetch failed with exit code') { $taskIsInfra = $true }; if ($taskIsInfra) { $hasInfraFailure = $true; continue }; $failedTasks += @{ name = $rec.name; issues = $issues; excerpt = $excerpt; failedTests = $failedTests } }; if ($hasInfraFailure -and $failedTasks.Count -eq 0 -and $b.status -eq 'completed') { $jsonHeaders = @{ Authorization = $headers.Authorization; 'Content-Type' = 'application/json' }; $stageRetried = $false; foreach ($st in @($tl.records | Where-Object { $_.type -eq 'Stage' -and $_.result -eq 'failed' -and $_.identifier })) { try { Invoke-RestMethod -Uri (\"$projectBase/_apis/build/builds/$($b.id)/stages/$($st.identifier)?api-version=7.0-preview.1\") -Headers $jsonHeaders -Method Patch -Body '{\"state\":1,\"forceRetryAllJobs\":false}' | Out-Null; $stageRetried = $true } catch {} }; if ($stageRetried) { $b.status = 'inProgress'; $b.result = $null } else { $needsPolicyRequeue = $true } }; if ($needsPolicyRequeue) { try { $projId = $pr.repository.project.id; $artId = [uri]::EscapeDataString(\"vstfs:///CodeReview/CodeReviewId/$projId/$($pr.pullRequestId)\"); $evals = (Invoke-RestMethod -Uri (\"$projectBase/_apis/policy/evaluations?artifactId=$artId&api-version=7.1-preview.1\") -Headers $headers -Method Get).value; $jsonHeaders = @{ Authorization = $headers.Authorization; 'Content-Type' = 'application/json' }; foreach ($ev in @($evals | Where-Object { $_.context.buildId -eq $b.id })) { Invoke-RestMethod -Method Patch -Uri (\"$projectBase/_apis/policy/evaluations/$($ev.evaluationId)?api-version=7.1-preview.1\") -Headers $jsonHeaders -Body '{}' | Out-Null }; $failedTasks = @(); $b.status = 'inProgress'; $b.result = $null } catch {} } } catch {} }; @{ id = $b.id; definition = $b.definition.name; buildNumber = $b.buildNumber; status = $b.status; result = $b.result; failedTasks = $failedTasks } })",
            "$runningDefNames = @($latestBuilds | Where-Object { $_.status -in @('inProgress','notStarted') -and $_.failedTasks.Count -eq 0 } | ForEach-Object { $_.definition.ToLowerInvariant() })",
            "foreach ($st in $latestStatuses) { if ($st.state -eq 'failed' -and $st.context.name -eq 'codecoverage' -and ($runningDefNames -contains ([string]$st.context.genre).ToLowerInvariant())) { $st.state = 'pending'; $st.description = 'Coverage status check pending active build completion' } }",
            `$out = @{ number = $pr.pullRequestId; title = $pr.title; state = $pr.status; mergeStateStatus = $pr.mergeStatus; headRefName = ($pr.sourceRefName -replace '^refs/heads/',''); headRefOid = $pr.lastMergeSourceCommit.commitId; baseRefName = ($pr.targetRefName -replace '^refs/heads/',''); statusCheckRollup = $latestStatuses; prBuilds = $latestBuilds; activeReviewThreads = $activeThreads; url = '${parsedPr.prUrl.replace(/'/g, "''")}' }`,
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "[Console]::Out.Write(($out | ConvertTo-Json -Depth 8 -Compress))",
        ].join("; ");
        const rawJson = exec("powershell", ["-NoProfile", "-Command", psScript], { cwd: workspaceRoot });
        return JSON.parse(rawJson);
    }

    const account = resolveGitHubAccount(
        { hostname: parsedPr.hostname, account: "" },
        workspaceRoot,
        exec,
    );
    const gitEnv = {
        GH_TOKEN: undefined,
        GITHUB_TOKEN: undefined,
    };
    if (nonempty(account)) {
        try {
            const token = exec("gh", ["auth", "token", "--user", account, "-h", parsedPr.hostname], { cwd: workspaceRoot });
            if (nonempty(token)) {
                gitEnv.GH_TOKEN = token;
                gitEnv.GITHUB_TOKEN = token;
            }
        } catch {
            // Fall back to ambient gh auth.
        }
    }
    const repoArg = parsedPr.hostname === "github.com"
        ? parsedPr.slug
        : `${parsedPr.hostname}/${parsedPr.slug}`;
    const rawJson = exec(
        "gh",
        [
            "pr",
            "view",
            String(parsedPr.prNumber),
            "--repo",
            repoArg,
            "--json",
            "number,title,state,mergeStateStatus,headRefName,headRefOid,baseRefName,statusCheckRollup,reviews,comments,url",
        ],
        { cwd: workspaceRoot, env: gitEnv },
    );
    const parsedSnapshot = JSON.parse(rawJson);

    if (exec === defaultExec && record(parsedSnapshot)) {
        try {
            const [owner, repoName] = String(parsedPr.slug || "").split("/");
            if (nonempty(owner) && nonempty(repoName)) {
                const gqlQuery = "query($owner: String!, $name: String!, $prNumber: Int!) { repository(owner: $owner, name: $name) { pullRequest(number: $prNumber) { reviewThreads(first: 50) { nodes { id isResolved path line comments(first: 10) { nodes { databaseId author { login } body } } } } } } }";
                const rawGql = exec(
                    "gh",
                    [
                        "api",
                        "--hostname",
                        parsedPr.hostname,
                        "graphql",
                        "-f",
                        `owner=${owner}`,
                        "-f",
                        `name=${repoName}`,
                        "-F",
                        `prNumber=${parsedPr.prNumber}`,
                        "-f",
                        `query=${gqlQuery}`,
                    ],
                    { cwd: workspaceRoot, env: gitEnv },
                );
                const parsedGql = JSON.parse(rawGql);
                const threadNodes = parsedGql?.data?.repository?.pullRequest?.reviewThreads?.nodes;
                if (Array.isArray(threadNodes)) {
                    const unresolved = threadNodes
                        .filter((t) => record(t) && t.isResolved === false)
                        .map((t) => {
                            const firstComment = Array.isArray(t.comments?.nodes) ? t.comments.nodes[0] : null;
                            return {
                                threadId: t.id,
                                id: firstComment?.databaseId,
                                path: t.path,
                                line: t.line,
                                author: firstComment?.author?.login,
                                body: firstComment?.body,
                            };
                        })
                        .filter((c) => nonempty(c.body));
                    if (unresolved.length > 0) {
                        parsedSnapshot.inlineReviewComments = unresolved;
                    }
                }
            }
        } catch {
            // Optional inline review thread enrichment.
        }

        const checks = Array.isArray(parsedSnapshot.statusCheckRollup) ? parsedSnapshot.statusCheckRollup : [];
        const adoBuildRefs = new Map();
        for (const check of checks) {
            const conclusion = String(check?.conclusion || check?.state || "").toLowerCase();
            if (conclusion !== "failure" && conclusion !== "failed") continue;
            const detailsUrl = String(check?.detailsUrl || "");
            const m = detailsUrl.match(/^(https:\/\/(?:[^/]+\.visualstudio\.com|dev\.azure\.com\/[^/]+)\/[^/]+)\/_build\/results\?[^\s"']*?\bbuildId=(\d+)/i);
            if (m) {
                const buildId = Number(m[2]);
                if (!adoBuildRefs.has(buildId)) {
                    adoBuildRefs.set(buildId, { projectBase: m[1], buildId, checkName: check?.name || `Build ${buildId}` });
                }
            }
        }

        if (adoBuildRefs.size > 0) {
            const entriesJson = JSON.stringify([...adoBuildRefs.values()]);
            const entriesB64 = Buffer.from(entriesJson, "utf8").toString("base64");
            const psCheckLogs = [
                "$ErrorActionPreference = 'Stop'",
                "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
                "$headers = @{ Authorization = \"Bearer $token\" }",
                `$entries = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('${entriesB64}')) | ConvertFrom-Json`,
                "$outBuilds = @()",
                "foreach ($e in $entries) {",
                "  $failedTasks = @()",
                "  try {",
                "    $tl = Invoke-RestMethod -Uri \"$($e.projectBase)/_apis/build/builds/$($e.buildId)/timeline?api-version=7.0\" -Headers $headers -Method Get",
                "    foreach ($rec in @($tl.records | Where-Object { $_.type -eq 'Task' -and $_.result -eq 'failed' })) {",
                "      $issues = @($rec.issues | ForEach-Object { $_.message })",
                "      $excerpt = @()",
                "      if ($rec.log.url) {",
                "        try {",
                "          $logText = Invoke-RestMethod -Uri ($rec.log.url + '?api-version=7.0') -Headers $headers -Method Get",
                "          $excerpt = @($logText -split \"`r?`n\" | Where-Object { $_ -notmatch '\\tat org\\.gradle\\.|\\tat java\\.base/' } | Select-Object -Last 30)",
                "        } catch {}",
                "      }",
                "      $failedTasks += @{ name = $rec.name; issues = $issues; excerpt = $excerpt }",
                "    }",
                "  } catch {}",
                "  $outBuilds += @{ id = $e.buildId; definition = $e.checkName; status = 'completed'; result = 'failed'; failedTasks = $failedTasks }",
                "}",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
                "[Console]::Out.Write(($outBuilds | ConvertTo-Json -Depth 6 -Compress))",
            ].join("; ");
            try {
                const rawBuilds = exec("powershell", ["-NoProfile", "-Command", psCheckLogs], { cwd: workspaceRoot });
                const parsedBuilds = JSON.parse(rawBuilds);
                parsedSnapshot.prBuilds = Array.isArray(parsedBuilds) ? parsedBuilds : [parsedBuilds];
            } catch {
                // Optional Azure Pipelines check log enrichment.
            }
        }
    }

    return parsedSnapshot;
}

export function isSnapshotWaitingForCiOnly(snapshot) {
    if (!record(snapshot)) return false;
    const mergeState = String(snapshot.mergeStateStatus || "").toLowerCase();
    if (mergeState.includes("conflict") || mergeState === "dirty") {
        return false;
    }

    const activeThreads = Array.isArray(snapshot.activeReviewThreads) ? snapshot.activeReviewThreads : [];
    const hasActionableThread = activeThreads.some((thread) => {
        const comments = Array.isArray(thread?.comments) ? thread.comments : [];
        return comments.some((c) => {
            const text = String(c?.content || "");
            if (/AuthApp PR Reviewer.*\bApprove\b.*No blocking issues found/is.test(text)) {
                return false;
            }
            return nonempty(text);
        });
    });
    if (hasActionableThread) return false;

    const inlineComments = Array.isArray(snapshot.inlineReviewComments) ? snapshot.inlineReviewComments : [];
    if (inlineComments.some((c) => nonempty(c?.body))) return false;

    const prBuilds = Array.isArray(snapshot.prBuilds) ? snapshot.prBuilds : [];
    let hasRunningBuild = false;
    for (const build of prBuilds) {
        const defName = String(build?.definition || "");
        const isNonBlockingAiReviewer = /PR-Reviewer|AI PR Review/i.test(defName);
        const failedTasks = Array.isArray(build?.failedTasks) ? build.failedTasks : [];
        if (!isNonBlockingAiReviewer && (failedTasks.length > 0 || String(build?.result || "").toLowerCase() === "failed")) {
            return false;
        }
        const status = String(build?.status || "").toLowerCase();
        if (status === "inprogress" || status === "notstarted" || status === "postponed") {
            hasRunningBuild = true;
        }
    }

    const checks = Array.isArray(snapshot.statusCheckRollup) ? snapshot.statusCheckRollup : [];
    let hasPendingCheck = false;
    for (const check of checks) {
        const state = String(check?.state || check?.status || "").toLowerCase();
        const conclusion = String(check?.conclusion || "").toLowerCase();
        const description = String(check?.description || "");
        const checkName = String(check?.context?.name || check?.name || "").toLowerCase();
        if (hasRunningBuild && (checkName === "codecoverage" || /Coverage status check failed/i.test(description))) {
            hasPendingCheck = true;
            continue;
        }
        if (state === "failed" || state === "error" || conclusion === "failure" || conclusion === "timed_out") {
            return false;
        }
        if (/Missing Approvals/i.test(description)) {
            continue;
        }
        if (
            state === "pending" ||
            state === "in_progress" ||
            state === "queued" ||
            (!nonempty(state) && /queued|in progress|processing/i.test(description))
        ) {
            hasPendingCheck = true;
        }
    }

    return hasRunningBuild || hasPendingCheck;
}

export function computeSnapshotFingerprint(snapshot) {
    if (!record(snapshot)) return "";
    const checks = Array.isArray(snapshot.statusCheckRollup)
        ? snapshot.statusCheckRollup.map((c) => ({
            name: c?.context?.name || c?.name || "",
            state: c?.state || c?.status || "",
            conclusion: c?.conclusion || "",
            description: c?.description || "",
        }))
        : [];
    const builds = Array.isArray(snapshot.prBuilds)
        ? snapshot.prBuilds.map((b) => ({
            id: b?.id || 0,
            definition: b?.definition || "",
            status: b?.status || "",
            result: b?.result || "",
            failedTasks: Array.isArray(b?.failedTasks)
                ? b.failedTasks.map((t) => `${t?.name || ""}:${(t?.failedTests || []).length}`)
                : [],
        }))
        : [];
    const threads = Array.isArray(snapshot.activeReviewThreads)
        ? snapshot.activeReviewThreads.map((t) => `${t?.id || ""}:${t?.status || ""}:${(t?.comments || []).length}`)
        : [];
    const inlineComments = Array.isArray(snapshot.inlineReviewComments)
        ? snapshot.inlineReviewComments.map((c) => `${c?.id || ""}:${c?.path || ""}:${c?.line || ""}`)
        : [];
    return createHash("sha256")
        .update(JSON.stringify({
            headRefOid: snapshot.headRefOid || "",
            mergeStateStatus: snapshot.mergeStateStatus || "",
            checks,
            builds,
            threads,
            inlineComments,
        }))
        .digest("hex")
        .slice(0, 16);
}

export function isTransientCiInfrastructureFailure(textValue) {
    return /UnknownHostException|SocketException|nodename nor servname|ETIMEDOUT|ECONNRESET|Connection reset|502 Bad Gateway|503 Service Unavailable|feed network|No source\/test defect was evidenced|canceled iteration|stale iteration|current-head.*running|no confirmed repair to make/i.test(
        String(textValue || ""),
    );
}

export function retryFailedAdoBuildStages(parsedPr, snapshot, { workspaceRoot = process.cwd(), exec = defaultExec } = {}) {
    if (parsedPr?.provider !== "ado") return false;
    const prBuilds = Array.isArray(snapshot?.prBuilds) ? snapshot.prBuilds : [];
    const failedBuildIds = prBuilds
        .filter((b) => !/PR-Reviewer|AI PR Review/i.test(String(b?.definition || "")) &&
            ((Array.isArray(b?.failedTasks) && b.failedTasks.length > 0) || String(b?.result || "").toLowerCase() === "failed"))
        .map((b) => Number(b.id))
        .filter(positiveInt);
    if (failedBuildIds.length === 0) return false;

    const orgUrl = (parsedPr.orgUrl || `https://${parsedPr.hostname}`).replace(/\/+$/, "").replace(/'/g, "''");
    const projectEnc = encodeURIComponent(parsedPr.project);
    const repoEnc = encodeURIComponent(parsedPr.repoName);
    const psScript = [
        "$ErrorActionPreference = 'Stop'",
        "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
        "$headers = @{ Authorization = \"Bearer $token\"; 'Content-Type' = 'application/json' }",
        `$projectBase = '${orgUrl}/${projectEnc}'`,
        "$retried = $false",
        `foreach ($bid in @(${failedBuildIds.join(",")})) {`,
        "  $stageRetried = $false",
        "  try {",
        "    $tl = Invoke-RestMethod -Uri \"$projectBase/_apis/build/builds/$bid/timeline?api-version=7.0\" -Headers $headers -Method Get",
        "    $stages = @($tl.records | Where-Object { $_.type -eq 'Stage' -and ($_.result -eq 'failed' -or $_.state -eq 'completed') -and $_.identifier })",
        "    foreach ($st in $stages) {",
        "      try {",
        "        Invoke-RestMethod -Uri \"$projectBase/_apis/build/builds/$bid/stages/$($st.identifier)?api-version=7.0-preview.1\" -Headers $headers -Method Patch -Body '{\"state\":1,\"forceRetryAllJobs\":false}' | Out-Null",
        "        $retried = $true; $stageRetried = $true",
        "      } catch {}",
        "    }",
        "  } catch {}",
        "  if (-not $stageRetried) {",
        "    try {",
        `      $pr = Invoke-RestMethod -Uri "$projectBase/_apis/git/repositories/${repoEnc}/pullRequests/${parsedPr.prNumber}?api-version=7.0" -Headers $headers -Method Get`,
        "      $projId = $pr.repository.project.id",
        `      $artId = [uri]::EscapeDataString("vstfs:///CodeReview/CodeReviewId/$projId/${parsedPr.prNumber}")`,
        "      $evals = (Invoke-RestMethod -Uri \"$projectBase/_apis/policy/evaluations?artifactId=$artId&api-version=7.1-preview.1\" -Headers $headers -Method Get).value",
        "      foreach ($ev in @($evals | Where-Object { $_.context.buildId -eq $bid })) {",
        "        Invoke-RestMethod -Method Patch -Uri \"$projectBase/_apis/policy/evaluations/$($ev.evaluationId)?api-version=7.1-preview.1\" -Headers $headers -Body '{}' | Out-Null",
        "        $retried = $true",
        "      }",
        "    } catch {}",
        "  }",
        "}",
        "[Console]::Out.Write(($retried | ConvertTo-Json))",
    ].join("; ");
    try {
        const res = exec("powershell", ["-NoProfile", "-Command", psScript], { cwd: workspaceRoot });
        return /true/i.test(res);
    } catch {
        return false;
    }
}

export function computeRepairIdentity({ observation, snapshot, worktreeSnap }) {
    const findings = Array.isArray(observation?.actionableFindings)
        ? [...observation.actionableFindings].map((s) => String(s || "").trim()).filter(Boolean).sort()
        : [];
    const headOid = snapshot?.headRefOid || snapshot?.headRefName || "HEAD";
    const baseRef = snapshot?.baseRefName || "dev";
    const payload = JSON.stringify([
        observation?.repairKind || "repair",
        headOid,
        baseRef,
        findings,
        worktreeSnap || "",
    ]);
    return createHash("sha256").update(payload).digest("hex").slice(0, 16);
}

export async function runPrObserver(ctx, dependencies = {}) {
    const args = record(ctx?.args) ? ctx.args : {};
    const rawOptions = record(args.options) ? args.options : {};
    const parsedPr = parsePullRequestUrl(args.prUrl);
    if (!parsedPr) {
        return {
            status: "needs-owner-decision",
            prUrl: args.prUrl || null,
            summary: "Supply a valid GitHub/GHE (https://<host>/<owner>/<repo>/pull/<number>) or Azure DevOps (https://<org>.visualstudio.com/<project>/_git/<repo>/pullrequest/<number>) pull request URL.",
            evidence: [],
            iterations: 0,
        };
    }

    const maxIterations = positiveInt(args.maxIterations ?? rawOptions.maxIterations)
        ? Math.min(Number(args.maxIterations ?? rawOptions.maxIterations), MAX_OBSERVER_ITERATIONS)
        : MAX_OBSERVER_ITERATIONS;
    const workspaceRoot = nonempty(rawOptions.workspaceRoot) ? rawOptions.workspaceRoot : process.cwd();
    const exec = dependencies.exec || defaultExec;
    const fetchSnapshot = dependencies.fetchPrSnapshot || ((pr) => fetchPullRequestSnapshot(pr, { workspaceRoot, exec }));
    const sleep = dependencies.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    const ciPollIntervalMs = Number.isFinite(rawOptions.ciPollIntervalMs) && rawOptions.ciPollIntervalMs >= 0
        ? Number(rawOptions.ciPollIntervalMs)
        : DEFAULT_CI_POLL_INTERVAL_MS;
    const maxCiPollAttempts = Number.isSafeInteger(rawOptions.maxCiPollAttempts) && rawOptions.maxCiPollAttempts >= 0
        ? Number(rawOptions.maxCiPollAttempts)
        : DEFAULT_MAX_CI_POLL_ATTEMPTS;
    const pushRepairs = rawOptions.pushRepairs !== false;
    const pauseOnBlocker = rawOptions.pauseOnBlocker !== undefined
        ? rawOptions.pauseOnBlocker === true && typeof ctx?.pause === "function"
        : typeof ctx?.pause === "function";

    const prefix = `pr-observer-v1-${createHash("sha256").update(parsedPr.prUrl).digest("hex").slice(0, 16)}`;
    const step = (key, producer, options) => ctx.step(`${prefix}-${key}`, producer, options);
    const phase = (title) => ctx.phase?.(title);
    const log = (message) => ctx.log?.(message);

    const target = {
        id: `${parsedPr.repository}-pr-${parsedPr.prNumber}`,
        repository: parsedPr.repository,
        localPath: parsedPr.localPath,
        hostname: parsedPr.hostname,
        slug: parsedPr.slug,
        baseBranch: "dev",
        account: "",
        verificationCommands: Array.isArray(args.verificationCommands) ? args.verificationCommands : [],
    };
    const repoRoot = path.resolve(workspaceRoot, target.localPath || ".");
    const builderGuides = resolveRoleSkillGuides("builder", [target]);
    const reviewerGuides = resolveRoleSkillGuides("reviewer", [target]);
    const attemptedRepairIdentities = new Set();

    for (let iteration = 0; iteration < maxIterations; iteration++) {
        phase("Observe");
        log(`Observing ${parsedPr.prUrl} (iteration ${iteration + 1}/${maxIterations})`);

        let snapshot = await step(`snapshot-${iteration}`, async () => fetchSnapshot(parsedPr), { volatile: true });
        let totalPollsUsed = 0;
        let pollSeq = 0;
        let buildResetCount = 0;
        const getBuildIdsKey = (s) => Array.isArray(s?.prBuilds) ? s.prBuilds.map((b) => b?.id || 0).join(",") : "";
        let activeBuildIdsKey = getBuildIdsKey(snapshot);
        let observation = null;
        let afterObserveSnap = snapshotWorktree(repoRoot, exec);
        let lastWaitingFingerprint = null;

        for (let ciWaitRound = 0; ciWaitRound <= maxCiPollAttempts; ciWaitRound++) {
            for (
                let batchPoll = 0;
                batchPoll < MAX_JS_POLLS_BEFORE_HEARTBEAT &&
                totalPollsUsed < maxCiPollAttempts &&
                (isSnapshotWaitingForCiOnly(snapshot) ||
                    (nonempty(lastWaitingFingerprint) && computeSnapshotFingerprint(snapshot) === lastWaitingFingerprint));
                batchPoll++
            ) {
                totalPollsUsed++;
                pollSeq++;
                log(`PR #${parsedPr.prNumber} CI checks are still running with no failures yet; polling again in ${Math.round(ciPollIntervalMs / 1000)}s (${totalPollsUsed}/${maxCiPollAttempts})...`);
                await sleep(ciPollIntervalMs);
                snapshot = await step(
                    `snapshot-${iteration}-poll-${pollSeq}`,
                    async () => fetchSnapshot(parsedPr),
                    { volatile: true },
                );
                const nextBuildIdsKey = getBuildIdsKey(snapshot);
                if (nonempty(nextBuildIdsKey) && nextBuildIdsKey !== activeBuildIdsKey && buildResetCount < MAX_OBSERVER_ITERATIONS) {
                    activeBuildIdsKey = nextBuildIdsKey;
                    buildResetCount++;
                    totalPollsUsed = 0;
                }
            }

            if (record(snapshot) && nonempty(snapshot.baseRefName)) {
                target.baseBranch = snapshot.baseRefName;
            }
            const currentFingerprint = computeSnapshotFingerprint(snapshot);
            const beforeObserveSnap = snapshotWorktree(repoRoot, exec);
            const observeGuides = resolveRoleSkillGuides("evidence", [target]);
            const stepKey = ciWaitRound === 0
                ? `observe-${iteration}`
                : `observe-${iteration}-wait-${ciWaitRound}`;
            observation = await step(
                stepKey,
                () => ctx.agent(
                    `Read and apply these repository instructions: ${observeGuides.join(", ")}.
Inspect the pull request snapshot and classify whether it is ready for human merge, needs a tactical repair (CI failure, review comment, or merge conflict), is waiting for CI, or needs an owner decision.\nFresh observation request: ${randomUUID()}\nDATA:\n${JSON.stringify({ pr: parsedPr, snapshot, iteration: iteration + 1 })}`,
                    {
                        label: `Observe PR #${parsedPr.prNumber} (round ${iteration + 1})`,
                        agent: OBSERVER_AGENT,
                        schema: observationSchema,
                    },
                ),
                { volatile: true },
            );
            afterObserveSnap = snapshotWorktree(repoRoot, exec);
            if (beforeObserveSnap && afterObserveSnap && beforeObserveSnap !== afterObserveSnap) {
                return {
                    status: "needs-owner-decision",
                    prUrl: parsedPr.prUrl,
                    summary: "Read-only observer unexpectedly modified the worktree.",
                    evidence: [],
                    iterations: iteration + 1,
                };
            }
            if (!record(observation) || observation.verdict !== "waiting-for-ci" || totalPollsUsed >= maxCiPollAttempts) {
                break;
            }
            lastWaitingFingerprint = currentFingerprint;
            totalPollsUsed++;
            log(`Observer reported waiting-for-ci on PR #${parsedPr.prNumber}; sleeping ${Math.round(ciPollIntervalMs / 1000)}s before re-checking (${totalPollsUsed}/${maxCiPollAttempts})...`);
            await sleep(ciPollIntervalMs);
            snapshot = await step(
                `snapshot-${iteration}-after-wait-${ciWaitRound + 1}`,
                async () => fetchSnapshot(parsedPr),
                { volatile: true },
            );
        }

        const headBranch = record(snapshot) && nonempty(snapshot.headRefName)
            ? snapshot.headRefName
            : `pr-${parsedPr.prNumber}`;

        if (!record(observation) || !nonempty(observation.verdict)) {
            return {
                status: "needs-owner-decision",
                prUrl: parsedPr.prUrl,
                summary: "Observer returned no valid structured classification.",
                evidence: [],
                iterations: iteration + 1,
            };
        }

        if (observation.verdict === "ready") {
            const hostLabel = parsedPr.provider === "ado" ? "Azure DevOps" : "GitHub/GHE";
            const readyStatusBlock = renderCurrentStatusBlock({
                status: currentStatuses.readyForHumanMerge,
                owner: currentStatusOwners.human,
                runId: ctx?.runId || "local",
                repository: parsedPr.repository,
                prUrl: parsedPr.prUrl,
                summary: observation.summary,
                nextAction: `Human review and merge in ${hostLabel}.`,
            });
            return {
                status: currentStatuses.readyForHumanMerge,
                prUrl: parsedPr.prUrl,
                repairKind: "none",
                summary: observation.summary,
                evidence: observation.evidence || [],
                iterations: iteration + 1,
                humanMergeOnly: true,
                statusBlock: readyStatusBlock,
            };
        }

        if (observation.verdict === "waiting-for-ci") {
            return {
                status: "waiting-for-ci",
                prUrl: parsedPr.prUrl,
                repairKind: observation.repairKind || "none",
                summary: observation.summary,
                evidence: observation.evidence || [],
                actionableFindings: observation.actionableFindings || [],
                iterations: iteration + 1,
            };
        }

        if (observation.verdict === "needs-owner-decision") {
            if (pauseOnBlocker) {
                await pauseForHelp(ctx, `${prefix}-${observation.verdict}-${iteration}`, observation.summary);
            }
            return {
                status: observation.verdict,
                prUrl: parsedPr.prUrl,
                repairKind: observation.repairKind || "none",
                summary: observation.summary,
                evidence: observation.evidence || [],
                actionableFindings: observation.actionableFindings || [],
                iterations: iteration + 1,
            };
        }

        const repairId = computeRepairIdentity({
            observation,
            snapshot,
            worktreeSnap: afterObserveSnap,
        });
        if (attemptedRepairIdentities.has(repairId)) {
            const repeatMsg = `Repeated identical tactical repair assignment (${repairId}) on ${parsedPr.prUrl}; pausing for human inspection instead of looping.`;
            if (pauseOnBlocker) {
                await pauseForHelp(ctx, `${prefix}-repeat-repair-${iteration}`, repeatMsg);
            }
            return {
                status: currentStatuses.needsOwnerDecision,
                prUrl: parsedPr.prUrl,
                repairKind: observation.repairKind,
                summary: repeatMsg,
                evidence: observation.evidence || [],
                actionableFindings: observation.actionableFindings || [],
                iterations: iteration + 1,
            };
        }
        attemptedRepairIdentities.add(repairId);

        // verdict === "repair"
        phase("Repair");
        try {
            let currentBranch = "";
            try {
                currentBranch = exec("git", ["rev-parse", "--abbrev-ref", "HEAD"], { cwd: repoRoot });
            } catch {
                currentBranch = "";
            }
            if (currentBranch !== headBranch) {
                ensureIsolatedTargetWorktree(workspaceRoot, { ...target, baseBranch: headBranch }, exec);
            }
        } catch {
            // Best-effort worktree preparation in mock/test environments.
        }

        const remoteCheck = verifyCheckoutRemoteMatchesPr(repoRoot, parsedPr, exec);
        if (!remoteCheck.ok) {
            if (pauseOnBlocker) {
                await pauseForHelp(ctx, `${prefix}-remote-mismatch-${iteration}`, remoteCheck.reason);
            }
            return {
                status: currentStatuses.needsOwnerDecision,
                prUrl: parsedPr.prUrl,
                repairKind: observation.repairKind,
                summary: remoteCheck.reason,
                evidence: [],
                iterations: iteration + 1,
            };
        }

        const repairResult = await step(`repair-${iteration}`, () => ctx.agent(
            `Read and follow these repository skill guides: ${builderGuides.join(", ")}.
Apply a surgical tactical fix in ${target.repository} for the reported PR findings (${observation.repairKind}):
${(observation.actionableFindings || []).map((f) => `- ${f}`).join("\n")}
Follow Android Auth PR maintenance rules:
- Never amend or force-push; keep commits incremental/fast-forward.
- Never modify CI pipeline YAML files (.pipelines/*.yml, .github/workflows/*.yml, azure-pipelines/*.yml) during a tactical repair unless the PR itself is a pipeline change.
- When verifying a CI test failure locally, run ONLY the targeted failing test class(es) with --tests "<ClassName>" --max-workers=2 (never run the unfiltered full-repo unitTestReport -Pcoverage task locally on Windows, where unrelated native SQLite/Room tests fail outside the macOS CI runner). Once the targeted test class(es) pass locally, return verdict "pass" so the fix is reviewed and pushed to CI.
- When merging origin/${target.baseBranch}, if a release was cut on ${target.baseBranch}, ensure this PR's CHANGELOG.md / changes.txt entry remains under the active vNext section; if ${target.baseBranch} deleted an adjacent retired flight enum entry, keep the retired flight deleted.
Do not broaden scope or push directly.\nDATA:\n${JSON.stringify({ pr: parsedPr, observation, snapshot })}`,
            {
                label: `Tactical repair (round ${iteration + 1})`,
                agent: BUILDER_AGENT,
                schema: slimResultSchema,
            },
        ));

        if (!record(repairResult) || repairResult.verdict === "blocked") {
            const blockerSummary = repairResult?.summary || "Tactical repair blocked.";
            const combinedInfraText = [
                blockerSummary,
                ...(Array.isArray( repairResult?.evidence) ? repairResult.evidence : []),
                ...(Array.isArray(observation?.evidence) ? observation.evidence : []),
            ].join("\n");
            if (isTransientCiInfrastructureFailure(combinedInfraText) && iteration + 1 < maxIterations) {
                log(`Detected transient CI infrastructure/network failure on PR #${parsedPr.prNumber}; waiting/retrying CI stage automatically without pausing...`);
                retryFailedAdoBuildStages(parsedPr, snapshot, { workspaceRoot, exec });
                await sleep(ciPollIntervalMs);
                continue;
            }
            if (pauseOnBlocker) {
                await pauseForHelp(ctx, `${prefix}-repair-blocked-${iteration}`, blockerSummary);
            }
            return {
                status: "needs-owner-decision",
                prUrl: parsedPr.prUrl,
                repairKind: observation.repairKind,
                summary: blockerSummary,
                evidence: repairResult?.evidence || [],
                iterations: iteration + 1,
            };
        }

        const revertedProtected = revertUnauthorizedProtectedFiles(repoRoot, {
            allowPipelineEdits: false,
            exec,
        });
        if (revertedProtected.length > 0) {
            log(`Reverted unauthorized CI pipeline / static-analysis config edits during tactical repair: ${revertedProtected.join(", ")}`);
        }

        if (
            target.verificationCommands.length > 0 &&
            (rawOptions.runLocalVerification === true || repairResult.verdict !== "pass")
        ) {
            const gate = runLocalVerificationGate({
                repoRoot,
                target,
                targetImpl: null,
                implementation: null,
                ...(dependencies.exec ? { exec: dependencies.exec } : {}),
            });
            if (gate.errors.length > 0) {
                log(`Local verification failed during tactical repair: ${gate.errors.join("; ")}`);
                continue;
            }
        }

        phase("Verify & Push");
        const reviewResult = await step(`review-repair-${iteration}`, () => ctx.agent(
            `Read and apply these repository code-review guides: ${reviewerGuides.join(", ")}.
Independently verify that the local tactical code/test repair in the worktree resolves the code/test defects in actionableFindings without regressions.
IMPORTANT: Git commit, git push, PR title/description metadata updates, and remote CI pipeline re-runs (such as Compare Jacoco Coverage or Validate PBI) are executed by the deterministic push-repair step immediately AFTER your review returns verdict "pass". Never return verdict "repair" or raise findings because changes are uncommitted/unpushed in the worktree or because remote CI checks have not re-run yet—return verdict "pass" with findings=[] as soon as the local code and unit-test changes are sound.\nDATA:\n${JSON.stringify({ pr: parsedPr, observation, repairResult })}`,
            {
                label: `Verify tactical repair (round ${iteration + 1})`,
                agent: REVIEWER_AGENT,
                schema: slimReviewSchema,
            },
        ));

        if (!record(reviewResult) || reviewResult.verdict !== "pass" ||
            (Array.isArray(reviewResult.findings) && reviewResult.findings.length > 0)) {
            log(`Tactical repair review requested further changes in iteration ${iteration + 1}.`);
            continue;
        }

        if (pushRepairs) {
            await step(`push-repair-${iteration}`, async () => {
                const account = resolveGitHubAccount(target, workspaceRoot, exec);
                const gitEnv = { GH_TOKEN: undefined, GITHUB_TOKEN: undefined };
                if (nonempty(account)) {
                    try {
                        const token = exec("gh", ["auth", "token", "--user", account, "-h", target.hostname], { cwd: workspaceRoot });
                        if (nonempty(token)) {
                            gitEnv.GH_TOKEN = token;
                            gitEnv.GITHUB_TOKEN = token;
                        }
                    } catch {
                        // Fall back to ambient auth.
                    }
                }
                const discovered = discoverChangedFilesFromGit(repoRoot, exec, {
                    allowPipelineEdits: false,
                });
                const isRealGitCheckout = exec === defaultExec && existsSync(path.join(repoRoot, ".git"));
                if (discovered.files.length > 0) {
                    for (const file of discovered.files) {
                        if (file.action === "delete") {
                            exec("git", ["rm", "--ignore-unmatch", "--", file.path], { cwd: repoRoot, env: gitEnv });
                        } else {
                            exec("git", ["add", "--", file.path], { cwd: repoRoot, env: gitEnv });
                        }
                    }
                    exec(
                        "git",
                        [
                            "commit",
                            "-m",
                            repairResult.prTitle || `Address PR #${parsedPr.prNumber} ${observation.repairKind}`,
                            "-m",
                            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
                        ],
                        { cwd: repoRoot, env: gitEnv },
                    );
                } else if (!isRealGitCheckout) {
                    exec("git", ["add", "-A"], { cwd: repoRoot, env: gitEnv });
                    exec(
                        "git",
                        [
                            "commit",
                            "-m",
                            repairResult.prTitle || `Address PR #${parsedPr.prNumber} ${observation.repairKind}`,
                            "-m",
                            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
                        ],
                        { cwd: repoRoot, env: gitEnv },
                    );
                }
                const pushArgs = nonempty(gitEnv.GH_TOKEN)
                    ? [
                        "-c",
                        `credential.https://${target.hostname}.helper=`,
                        "-c",
                        `credential.https://${target.hostname}.helper=!gh auth git-credential`,
                        "push",
                        "origin",
                        `HEAD:${headBranch}`,
                    ]
                    : ["push", "origin", `HEAD:${headBranch}`];
                exec("git", pushArgs, { cwd: repoRoot, env: gitEnv });
                let pushedSha = "";
                if (exec === defaultExec) {
                    try {
                        pushedSha = exec("git", ["rev-parse", "--short", "HEAD"], { cwd: repoRoot, env: gitEnv });
                    } catch {
                        pushedSha = "";
                    }
                }
                const changedFilePaths = discovered.files.map((f) => f.path);
                if (
                    parsedPr.provider === "ado" &&
                    Array.isArray(snapshot?.activeReviewThreads) &&
                    snapshot.activeReviewThreads.length > 0
                ) {
                    const addressedAdoThreads = selectAddressedReviewThreads({
                        threads: snapshot.activeReviewThreads,
                        observation,
                        repairResult,
                        changedFiles: changedFilePaths,
                    });
                    const orgUrl = (parsedPr.orgUrl || `https://${parsedPr.hostname}`).replace(/\/+$/, "").replace(/'/g, "''");
                    const projectEnc = encodeURIComponent(parsedPr.project);
                    const repoEnc = encodeURIComponent(parsedPr.repoName);
                    const threadIds = addressedAdoThreads
                        .map((t) => Number(t?.id))
                        .filter(positiveInt);
                    if (threadIds.length > 0) {
                        const replyB64 = Buffer.from(
                            JSON.stringify({
                                content: `Addressed in ${pushedSha || "latest commit"}: ${repairResult.summary || "applied verified tactical repair"}.`,
                                parentCommentId: 1,
                                commentType: 1,
                            }),
                            "utf8",
                        ).toString("base64");
                        const psResolveAdo = [
                            "$ErrorActionPreference = 'Stop'",
                            "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
                            "$headers = @{ Authorization = \"Bearer $token\"; 'Content-Type' = 'application/json' }",
                            `$base = '${orgUrl}/${projectEnc}/_apis/git/repositories/${repoEnc}/pullRequests/${parsedPr.prNumber}/threads'`,
                            `$replyBytes = [System.Convert]::FromBase64String('${replyB64}')`,
                            "$statusBytes = [System.Text.Encoding]::UTF8.GetBytes('{\"status\":\"fixed\"}')",
                            `foreach ($tid in @(${threadIds.join(",")})) {`,
                            "  try { Invoke-RestMethod -Uri \"$base/$($tid)/comments?api-version=7.0\" -Headers $headers -Method Post -Body $replyBytes | Out-Null } catch {}",
                            "  try { Invoke-RestMethod -Uri \"$base/$($tid)?api-version=7.0\" -Headers $headers -Method Patch -Body $statusBytes | Out-Null } catch {}",
                            "}",
                        ].join("; ");
                        try {
                            exec("powershell", ["-NoProfile", "-Command", psResolveAdo], { cwd: workspaceRoot });
                        } catch {
                            // Best-effort ADO thread reply and resolution after push.
                        }
                    }
                }
                if (
                    parsedPr.provider === "github" &&
                    Array.isArray(snapshot?.inlineReviewComments) &&
                    snapshot.inlineReviewComments.length > 0
                ) {
                    const addressedGithubThreads = selectAddressedReviewThreads({
                        threads: snapshot.inlineReviewComments,
                        observation,
                        repairResult,
                        changedFiles: changedFilePaths,
                    });
                    for (const thread of addressedGithubThreads) {
                        try {
                            if (positiveInt(Number(thread?.id))) {
                                exec(
                                    "gh",
                                    [
                                        "api",
                                        "--hostname",
                                        parsedPr.hostname,
                                        "--method",
                                        "POST",
                                        `repos/${parsedPr.slug}/pulls/${parsedPr.prNumber}/comments/${Number(thread.id)}/replies`,
                                        "-f",
                                        `body=Addressed in ${pushedSha || "latest commit"}: ${repairResult.summary || "applied verified tactical repair"}.`,
                                    ],
                                    { cwd: workspaceRoot, env: gitEnv },
                                );
                            }
                            if (nonempty(thread?.threadId)) {
                                const mut = "mutation($threadId: ID!) { resolveReviewThread(input: {threadId: $threadId}) { thread { isResolved } } }";
                                exec(
                                    "gh",
                                    [
                                        "api",
                                        "--hostname",
                                        parsedPr.hostname,
                                        "graphql",
                                        "-f",
                                        `threadId=${String(thread.threadId)}`,
                                        "-f",
                                        `query=${mut}`,
                                    ],
                                    { cwd: workspaceRoot, env: gitEnv },
                                );
                            }
                        } catch {
                            // Best-effort review thread reply and resolution after push.
                        }
                    }
                }
                return true;
            });
        }
    }

    return {
        status: "max-iterations-reached",
        prUrl: parsedPr.prUrl,
        summary: `Completed ${maxIterations} observer iteration(s) without reaching ready-for-human-merge.`,
        evidence: [],
        iterations: maxIterations,
    };
}

export const observerWorkflowDefinition = {
    meta: {
        name: "pr-observer",
        description: "Observe an Android Auth pull request's CI pipelines and review threads, apply verified tactical repairs, and hand off when ready for human merge.",
        phases: [
            { title: "Observe" },
            { title: "Repair" },
            { title: "Verify & Push" },
        ],
        argsSchema: {
            type: "object",
            properties: {
                prUrl: { type: "string" },
                repository: { type: "string" },
                maxIterations: { type: "integer" },
                verificationCommands: { type: "array", items: { type: "string" } },
                options: { type: "object" },
            },
            required: ["prUrl"],
        },
        limits: {
            maxConcurrentSubagents: 1,
            maxTotalSubagents: 50,
        },
    },
    run: runPrObserver,
};
