// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
const revisionedModuleUrl = (name) => {
    const url = new URL(name, import.meta.url);
    return `${url.href}?v=${statSync(url).mtimeMs}`;
};
const {
    currentStatusOwners,
    currentStatuses,
    deliveryStatusSummary,
    observerNextAction,
    renderCurrentStatusBlock,
    parseMarkerBlock,
    renderDeliveryDecisionBlock,
    renderVerificationBlock,
    deliveryDecisionMarkers,
    fitAdoDescription,
    verificationMarkers,
    statusMarkers,
    upsertMarkerBlock,
} = await import(revisionedModuleUrl("./status.mjs"));
const {
    BUILDER_AGENT,
    REVIEWER_AGENT,
    buildGitHubKeyringEnv,
    buildGitHubTokenOverrides,
    buildNormalizedProcessEnv,
    assertNoStagedLocalSdkConfiguration,
    discoverChangedFilesFromGit,
    ensureIsolatedTargetWorktree,
    isDefinitiveCodeOrTestFailure,
    pauseForHelp,
    resolveGitHubAccount,
    resolveRoleSkillGuides,
    resolveTargetJavaHome,
    revertUnauthorizedProtectedFiles,
    runLocalVerificationGate,
    slimResultSchema,
    slimReviewSchema,
    snapshotWorktree,
} = await import(revisionedModuleUrl("./workflow.mjs"));
const { ciVerificationSummary, localVerificationSummary, verificationSummary } = await import(revisionedModuleUrl("./verification.mjs"));
const { recordMilestone } = await import(revisionedModuleUrl("./milestones.mjs"));

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
const repairLimit = (args) => {
    const requested = args?.maxIterations ?? args?.options?.maxIterations;
    return Number.isSafeInteger(requested) && requested >= 0 ? Math.min(requested, MAX_OBSERVER_ITERATIONS) : MAX_OBSERVER_ITERATIONS;
};

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
    const execEnv = options.env
        ? (options.env.__completeEnv ? options.env : buildNormalizedProcessEnv(process.env, options.env))
        : buildNormalizedProcessEnv(process.env);
    return execFileSync(command, args, {
        cwd: options.cwd,
        env: execEnv,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
        timeout: options.timeout ?? 120_000,
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

export function parsePullRequestUrl(prUrl, trustedRepository = null) {
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
        const trustedAdo = trustedRepository?.provider === "ado" && trustedRepository.hostname === "dev.azure.com" &&
            String(trustedRepository.slug || "").toLowerCase() === adoKey && adoMatch[1]
            ? { org: rawOrg, project: rawProject, repoName: rawRepoName,
                repository: trustedRepository.repository, localPath: trustedRepository.localPath } : null;
        const allowedAdo = trustedRepository ? trustedAdo : ALLOWED_ADO_PR_REPOS[adoKey];
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
    const trustedGh = trustedRepository?.provider === "github" &&
        `${trustedRepository.hostname}/${trustedRepository.slug}`.toLowerCase() === ghKey
        ? trustedRepository : null;
    const allowedGh = trustedRepository ? trustedGh : ALLOWED_GITHUB_PR_REPOS[ghKey];
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
        if (hasDotGit) {
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

/**
 * @param {{ threads?: Record<string, unknown>[], observation?: Record<string, unknown> | null,
 * repairResult?: Record<string, unknown> | null, changedFiles?: string[] }} [input]
 */
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

export function buildAdoSnapshotScript(parsedPr, trustedRepository = null) {
    const allowed = parsePullRequestUrl(parsedPr?.prUrl, trustedRepository);
    if (allowed?.provider !== "ado") throw new Error("An allowlisted ADO PR is required.");
    const config = Buffer.from(JSON.stringify({
        orgUrl: allowed.orgUrl, project: allowed.project, repoName: allowed.repoName,
        prNumber: allowed.prNumber, prUrl: allowed.prUrl,
    })).toString("base64");
    const source = readFileSync(new URL("./ado-snapshot.ps1", import.meta.url), "utf8");
    return `$snapshotInputBase64 = '${config}'\n${source}`;
}

export function fetchPullRequestSnapshot(parsedPr, { workspaceRoot = process.cwd(), exec = defaultExec, trustedRepository = null } = {}) {
    if (parsedPr?.provider === "ado") {
        return scopeAdoSnapshot(JSON.parse(exec("powershell", ["-NoProfile", "-Command", buildAdoSnapshotScript(parsedPr, trustedRepository)], { cwd: workspaceRoot })));
    }

    const account = resolveGitHubAccount(
        { hostname: parsedPr.hostname, account: trustedRepository?.account || "" },
        workspaceRoot,
        exec,
    );
    let resolvedToken = "";
    if (nonempty(account)) {
        try {
            const token = exec("gh", ["auth", "token", "--user", account, "-h", parsedPr.hostname], {
                cwd: workspaceRoot,
                env: buildGitHubKeyringEnv(),
            });
            if (nonempty(token)) {
                resolvedToken = token;
            }
        } catch (error) {
            if (trustedRepository) throw error;
            // Fall back to ambient gh auth for standalone observer runs.
        }
    }
    const tokenOverrides = buildGitHubTokenOverrides(parsedPr.hostname, resolvedToken);
    if (trustedRepository && !nonempty(tokenOverrides.GH_TOKEN)) throw new Error("The configured GitHub account could not be authenticated.");
    const gitEnv = buildNormalizedProcessEnv(process.env, tokenOverrides);
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
            "number,title,body,state,mergeStateStatus,headRefName,headRefOid,baseRefName,statusCheckRollup,reviews,comments,files,url",
        ],
        { cwd: workspaceRoot, env: gitEnv },
    );
    const parsedSnapshot = JSON.parse(rawJson);
    if (record(parsedSnapshot) && Array.isArray(parsedSnapshot.files) && !Array.isArray(parsedSnapshot.changedFiles)) {
        parsedSnapshot.changedFiles = parsedSnapshot.files
            .map((f) => String(f?.path ?? "").trim().replace(/^\/+/, ""))
            .filter(nonempty)
            .slice(0, 50);
    }

    if (record(parsedSnapshot)) {
        try {
            const [owner, repoName] = String(parsedPr.slug || "").split("/");
            if (nonempty(owner) && nonempty(repoName)) {
                const gqlQuery = "query($owner: String!, $name: String!, $prNumber: Int!) { repository(owner: $owner, name: $name) { pullRequest(number: $prNumber) { reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { id isResolved path line comments(first: 100) { pageInfo { hasNextPage } nodes { databaseId author { login } body } } } } } } }";
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
                const threads = parsedGql?.data?.repository?.pullRequest?.reviewThreads;
                const threadNodes = threads?.nodes;
                parsedSnapshot.reviewThreadsComplete = Array.isArray(threadNodes) && threads?.pageInfo?.hasNextPage === false;
                if (Array.isArray(threadNodes)) {
                    parsedSnapshot.unresolvedReviewThreadCount = threadNodes.filter((t) => record(t) && t.isResolved !== true).length;
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
                                comments: t.comments?.nodes || [],
                                commentsComplete: t.comments?.pageInfo?.hasNextPage === false,
                            };
                        })
                        .filter((c) => nonempty(c.body));
                    if (unresolved.length > 0) {
                        parsedSnapshot.inlineReviewComments = unresolved;
                    }
                }
            }
        } catch {
            parsedSnapshot.reviewThreadsComplete = false;
            (parsedSnapshot.diagnosticErrors ||= []).push("GitHub inline review thread diagnostics unavailable.");
        }

        if (Array.isArray(parsedSnapshot.statusCheckRollup)) {
            parsedSnapshot.statusCheckRollup = deduplicateStatusCheckRollup(parsedSnapshot.statusCheckRollup);
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
                "if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Azure DevOps authentication failed for check diagnostics.' }",
                "$headers = @{ Authorization = \"Bearer $token\" }",
                `$entries = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('${entriesB64}')) | ConvertFrom-Json`,
                "$outBuilds = @()",
                "foreach ($e in $entries) {",
                "  $failedTasks = @()",
                "  $readErrors = @()",
                "  try {",
                "    $tl = Invoke-RestMethod -Uri \"$($e.projectBase)/_apis/build/builds/$($e.buildId)/timeline?api-version=7.0\" -Headers $headers -Method Get",
                "    foreach ($rec in @($tl.records | Where-Object { $_.type -eq 'Task' -and $_.result -eq 'failed' })) {",
                "      $issues = @($rec.issues | ForEach-Object { $_.message })",
                "      $excerpt = @()",
                "      if ($rec.log.url) {",
                "        try {",
                "          $logText = Invoke-RestMethod -Uri ($rec.log.url + '?api-version=7.0') -Headers $headers -Method Get",
                "          $cleanLines = @($logText -split \"`r?`n\" | Where-Object { $_ -notmatch '\\tat org\\.gradle\\.|\\tat java\\.base/|azure-pipelines-task-lib\\\\toolrunner\\.js|node:internal/child_process|Test results publishing|TestResultLogParser:' })",
                "          $failHits = @($cleanLines | Select-String -Pattern '\\bFAILED\\b|Exception\\b|##\\[error\\]|\\berror:' -Context 0,2 | ForEach-Object { @($_.Line) + @($_.Context.PostContext) } | Select-Object -First 40)",
                "          $tailHits = @($cleanLines | Select-Object -Last 45)",
                "          $excerpt = @($failHits + $tailHits | Select-Object -Unique)",
                "        } catch { $readErrors += \"Task $($rec.name) log unavailable: $($_.Exception.Message)\" }",
                "      }",
                "      $failedTasks += @{ name = $rec.name; issues = $issues; excerpt = $excerpt }",
                "    }",
                "  } catch { $readErrors += \"Build $($e.buildId) timeline unavailable: $($_.Exception.Message)\" }",
                "  $outBuilds += @{ id = $e.buildId; definition = $e.checkName; status = 'completed'; result = 'failed'; failedTasks = $failedTasks; diagnosticErrors = $readErrors }",
                "}",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
                "[Console]::Out.Write(($outBuilds | ConvertTo-Json -Depth 6 -Compress))",
            ].join("; ");
            try {
                const rawBuilds = exec("powershell", ["-NoProfile", "-Command", psCheckLogs], { cwd: workspaceRoot });
                const parsedBuilds = JSON.parse(rawBuilds);
                parsedSnapshot.prBuilds = Array.isArray(parsedBuilds) ? parsedBuilds : [parsedBuilds];
            } catch {
                (parsedSnapshot.diagnosticErrors ||= []).push("Azure Pipelines check log diagnostics unavailable.");
            }
        }
    }

    return parsedSnapshot;
}

export function hasActionableReviewOrCommentFeedback(snapshot) {
    if (!record(snapshot)) return false;

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
    if (hasActionableThread) return true;

    const inlineComments = Array.isArray(snapshot.inlineReviewComments) ? snapshot.inlineReviewComments : [];
    if (inlineComments.some((c) => nonempty(c?.body))) return true;

    const reviews = Array.isArray(snapshot.reviews) ? snapshot.reviews : [];
    if (reviews.length > 0) {
        const latestStateByReviewer = new Map();
        const latestActionableBodyByReviewer = new Map();
        for (let i = 0; i < reviews.length; i++) {
            const review = reviews[i];
            const state = String(review?.state || "").trim().toUpperCase();
            const author = String(review?.author?.login || review?.user?.login || `reviewer-${i}`).trim().toLowerCase();
            const body = String(review?.body || "").trim();
            const isBoilerplateBody =
                !nonempty(body) ||
                /<!--\s*ccr-overview/i.test(body) ||
                /AuthApp PR Reviewer.*\bApprove\b.*No blocking issues found/is.test(body);

            if (state === "APPROVED" || state === "DISMISSED") {
                latestStateByReviewer.set(author, state);
                latestActionableBodyByReviewer.delete(author);
                continue;
            }
            if (state === "CHANGES_REQUESTED") {
                latestStateByReviewer.set(author, state);
            }
            if (!isBoilerplateBody) {
                latestActionableBodyByReviewer.set(author, body);
            }
        }
        for (const state of latestStateByReviewer.values()) {
            if (state === "CHANGES_REQUESTED") {
                return true;
            }
        }
        if (latestActionableBodyByReviewer.size > 0) {
            return true;
        }
    }

    const topComments = Array.isArray(snapshot.comments) ? snapshot.comments : [];
    for (const comment of topComments) {
        const author = String(comment?.author?.login || comment?.user?.login || "").trim().toLowerCase();
        const body = String(comment?.body || comment?.content || "").trim();
        if (!nonempty(body)) continue;
        if (
            author === "azure-pipelines" ||
            author === "github-actions" ||
            /<!--\s*AzurePipelinesStatusComment\s*-->/i.test(body) ||
            /<!--\s*code:\s*lcc-\d+\s*-->/i.test(body) ||
            /^Addressed in [0-9a-f]{6,40}:/i.test(body)
        ) {
            continue;
        }
        return true;
    }

    return false;
}

export function isSnapshotWaitingForCiOnly(snapshot) {
    snapshot = scopeAdoSnapshot(snapshot);
    if (!record(snapshot)) return false;
    if (snapshot.snapshotHeadChanged) return true;
    const mergeState = String(snapshot.mergeStateStatus || "").toLowerCase();
    if (mergeState.includes("conflict") || mergeState === "dirty") {
        return false;
    }

    if (hasActionableReviewOrCommentFeedback(snapshot)) return false;

    const prBuilds = Array.isArray(snapshot.prBuilds) ? snapshot.prBuilds : [];
    let hasRunningBuild = false;
    for (const build of prBuilds) {
        const defName = String(build?.definition || "");
        const isNonBlockingAiReviewer = /PR-Reviewer|AI PR Review/i.test(defName);
        const failedTasks = Array.isArray(build?.failedTasks) ? build.failedTasks : [];
        if (!isNonBlockingAiReviewer && (failedTasks.length > 0 ||
            ["failed", "canceled", "cancelled", "partiallysucceeded"].includes(String(build?.result || "").toLowerCase()))) {
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
        if (isHumanApprovalCheck(check)) continue;
        if (hasRunningBuild && (checkName === "codecoverage" || /Coverage status check failed/i.test(description))) {
            hasPendingCheck = true;
            continue;
        }
        if (
            state === "failed" ||
            state === "failure" ||
            state === "error" ||
            conclusion === "failure" ||
            conclusion === "timed_out" ||
            conclusion === "cancelled" ||
            conclusion === "action_required"
        ) {
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

export function isSnapshotReadyForHumanMerge(snapshot) {
    snapshot = scopeAdoSnapshot(snapshot);
    if (!record(snapshot)) return false;
    if (snapshot.snapshotHeadChanged) return false;
    if (snapshot.provider === "ado" && ciEvidenceFromSnapshot(snapshot).status !== "passed") return false;
    if (isSnapshotWaitingForCiOnly(snapshot)) return false;

    const mergeState = String(snapshot.mergeStateStatus || "").toLowerCase();
    if (!["clean", "succeeded", "has_hooks"].includes(mergeState)) {
        return false;
    }

    if (hasActionableReviewOrCommentFeedback(snapshot)) return false;

    const prBuilds = Array.isArray(snapshot.prBuilds) ? snapshot.prBuilds : [];
    const checks = Array.isArray(snapshot.statusCheckRollup) ? snapshot.statusCheckRollup : [];
    if (prBuilds.length === 0 && checks.length === 0) {
        return false;
    }

    for (const build of prBuilds) {
        const defName = String(build?.definition || "");
        const isNonBlockingAiReviewer = /PR-Reviewer|AI PR Review/i.test(defName);
        const status = String(build?.status || "").toLowerCase();
        const result = String(build?.result || "").toLowerCase();
        const failedTasks = Array.isArray(build?.failedTasks) ? build.failedTasks : [];
        if (status !== "completed") return false;
        if (
            !isNonBlockingAiReviewer &&
            (failedTasks.length > 0 || result !== "succeeded")
        ) {
            return false;
        }
    }

    for (const check of checks) {
        const state = String(check?.state || check?.status || "").toLowerCase();
        const conclusion = String(check?.conclusion || "").toLowerCase();
        const description = String(check?.description || "");
        if (/Missing Approvals/i.test(description)) {
            continue;
        }
        const checkPassed =
            state === "succeeded" ||
            state === "success" ||
            state === "notapplicable" ||
            conclusion === "success" ||
            conclusion === "neutral" ||
            conclusion === "skipped";
        if (!checkPassed) {
            return false;
        }
    }

    return true;
}

export function listPrChangedFiles(repoRoot, snapshotOrBaseBranch = "dev", exec = defaultExec) {
    const snapshot = record(snapshotOrBaseBranch) ? snapshotOrBaseBranch : null;
    if (snapshot) {
        const providerFiles = Array.isArray(snapshot.changedFiles)
            ? snapshot.changedFiles
            : Array.isArray(snapshot.files)
                ? snapshot.files
                : null;
        if (Array.isArray(providerFiles) && providerFiles.length > 0) {
            return providerFiles
                .map((entry) => String(entry?.path ?? entry ?? "").trim().replace(/^\/+/, ""))
                .filter(nonempty)
                .slice(0, 50);
        }
    }

    if (!nonempty(repoRoot) || !existsSync(path.join(repoRoot, ".git"))) return null;
    const baseBranch = nonempty(snapshot?.baseRefName)
        ? snapshot.baseRefName.trim()
        : typeof snapshotOrBaseBranch === "string" && nonempty(snapshotOrBaseBranch)
            ? snapshotOrBaseBranch.trim()
            : "dev";
    const headOid = nonempty(snapshot?.headRefOid) ? snapshot.headRefOid.trim() : "";
    if (!nonempty(headOid)) {
        return null;
    }
    try {
        const diffOut = exec(
            "git",
            ["--no-pager", "diff", "--name-only", `origin/${baseBranch}...${headOid}`],
            { cwd: repoRoot },
        ) || "";
        const files = diffOut
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(nonempty)
            .slice(0, 50);
        return files.length > 0 ? files : null;
    } catch {
        return null;
    }
}

export function deduplicateStatusCheckRollup(checks) {
    if (!Array.isArray(checks) || checks.length <= 1) return checks;
    const parseMillis = (value) => {
        if (typeof value !== "string" || !value.trim()) return Number.NaN;
        const parsed = Date.parse(value);
        return Number.isFinite(parsed) ? parsed : Number.NaN;
    };
    const parseActionsIds = (entry) => {
        const rawUrl = String(entry?.detailsUrl || entry?.targetUrl || entry?.url || "");
        const match = rawUrl.match(/\/actions\/runs\/(\d+)(?:\/job\/(\d+))?/i);
        if (!match) return null;
        return {
            runId: BigInt(match[1]),
            jobId: match[2] ? BigInt(match[2]) : 0n,
        };
    };
    const isNewer = (candidate, current) => {
        const candStart = parseMillis(candidate?.startedAt || candidate?.createdAt);
        const currStart = parseMillis(current?.startedAt || current?.createdAt);
        if (Number.isFinite(candStart) && Number.isFinite(currStart) && candStart !== currStart) {
            return candStart > currStart;
        }
        const candDone = parseMillis(candidate?.completedAt || candidate?.updatedAt);
        const currDone = parseMillis(current?.completedAt || current?.updatedAt);
        if (Number.isFinite(candDone) && Number.isFinite(currDone) && candDone !== currDone) {
            return candDone > currDone;
        }
        const candIds = parseActionsIds(candidate);
        const currIds = parseActionsIds(current);
        if (candIds && currIds) {
            if (candIds.runId !== currIds.runId) return candIds.runId > currIds.runId;
            if (candIds.jobId !== currIds.jobId) return candIds.jobId > currIds.jobId;
        }
        return true;
    };
    const keyFor = (entry) => {
        if (!record(entry)) return "";
        const typeName = String(entry.__typename || "").trim();
        const contextGenre = record(entry.context) && typeof entry.context.genre === "string"
            ? entry.context.genre.trim()
            : "";
        const rawContext = typeof entry.context === "string"
            ? entry.context.trim()
            : record(entry.context) && typeof entry.context.name === "string"
                ? entry.context.name.trim()
                : "";
        const rawName = typeof entry.name === "string" ? entry.name.trim() : "";
        const workflowName = typeof entry.workflowName === "string" ? entry.workflowName.trim() : "";
        if (typeName === "StatusContext" || (rawContext && !rawName)) {
            return `status:${contextGenre.toLowerCase()}::${rawContext.toLowerCase()}`;
        }
        const checkName = rawName || rawContext;
        if (!checkName) return "";
        return `check:${workflowName.toLowerCase()}::${checkName.toLowerCase()}`;
    };
    const latestByKey = new Map();
    const unkeyed = [];
    checks.forEach((entry, index) => {
        const key = keyFor(entry);
        if (!key) {
            unkeyed.push({ index, entry });
            return;
        }
        const existing = latestByKey.get(key);
        if (!existing || isNewer(entry, existing.entry)) {
            latestByKey.set(key, { index, entry });
        }
    });
    return [...unkeyed, ...latestByKey.values()]
        .sort((a, b) => a.index - b.index)
        .map((item) => item.entry);
}

export function computeSnapshotFingerprint(snapshot) {
    if (!record(snapshot)) return "";
    snapshot = scopeAdoSnapshot(snapshot);
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

function classificationFingerprint(snapshot) {
    const metadata = new Set([
        "createdAt", "updatedAt", "submittedAt", "createdDate", "creationDate", "publishedDate", "lastUpdatedDate",
        "queueTime", "startTime", "finishTime", "observedAt", "collectedAt", "avatarUrl", "imageUrl", "_links",
    ]);
    const normalize = (value) => {
        if (Array.isArray(value)) return value.map(normalize);
        if (value && typeof value === "object") {
            return Object.fromEntries(Object.keys(value).filter((key) => !metadata.has(key)).sort().map((key) => {
                const normalized = normalize(value[key]);
                return [key, ["statusCheckRollup", "prBuilds", "policyEvaluations", "activeReviewThreads"].includes(key) && Array.isArray(normalized)
                    ? normalized.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))) : normalized];
            }));
        }
        return value;
    };
    return createHash("sha256").update(JSON.stringify(normalize(scopeAdoSnapshot(snapshot)))).digest("hex");
}

export function isTransientCiInfrastructureFailure(textValue) {
    return /UnknownHostException|SocketException|nodename nor servname|ETIMEDOUT|ECONNRESET|Connection reset|502 Bad Gateway|503 Service Unavailable|feed network|No source\/test defect was evidenced|canceled iteration|stale iteration|current-head.*running|no confirmed repair to make/i.test(
        String(textValue || ""),
    );
}

function adoObserverTransport(parsedPr, workspaceRoot, exec) {
    const request = (suffix, method = "Get", payload = null) => {
        const base = `${parsedPr.orgUrl || `https://${parsedPr.hostname}`}/${encodeURIComponent(parsedPr.project)}/_apis/`;
        const uri = (base + suffix).replace(/'/g, "''");
        const body = Buffer.from(JSON.stringify(payload || {})).toString("base64");
        const script = [
            "$ErrorActionPreference = 'Stop'",
            "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
            "if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Azure DevOps authentication failed for observer recovery.' }",
            "$headers = @{ Authorization = \"Bearer $token\"; 'Content-Type' = 'application/json' }",
            `$result = Invoke-RestMethod -Uri '${uri}' -Headers $headers -Method ${method}` +
                (method === "Get" ? "" : ` -Body ([Convert]::FromBase64String('${body}'))`),
            method === "Get" ? "[Console]::Out.Write(($result | ConvertTo-Json -Depth 30 -Compress))" : "[Console]::Out.Write('true')",
        ].join("; ");
        return JSON.parse(exec("powershell", ["-NoProfile", "-Command", script], { cwd: workspaceRoot }));
    };
    return {
        timeline: async (id) => request(`build/builds/${id}/timeline?api-version=7.0`).records,
        policies: async () => {
            const pr = request(`git/repositories/${encodeURIComponent(parsedPr.repoName)}/pullRequests/${parsedPr.prNumber}?api-version=7.0`);
            const artifact = encodeURIComponent(`vstfs:///CodeReview/CodeReviewId/${pr.repository.project.id}/${parsedPr.prNumber}`);
            return request(`policy/evaluations?artifactId=${artifact}&api-version=7.1-preview.1`).value;
        },
        retryStage: async (id, stage) => request(`build/builds/${id}/stages/${encodeURIComponent(stage.identifier)}?api-version=7.0-preview.1`,
            "Patch", { state: 1, forceRetryAllJobs: false }) === true,
        requeuePolicy: async (policy) => request(`policy/evaluations/${policy.evaluationId}?api-version=7.1-preview.1`, "Patch") === true,
    };
}

/**
 * The transport owns authentication, admission and durable effects; selection and fallback stay shared.
 * @param {{provider: string}} parsedPr
 * @param {{prBuilds?: unknown[]}} snapshot
 * @param {{ workspaceRoot?: string, exec?: typeof defaultExec, stageOnly?: boolean, transport?: {
 * timeline(id: number): Promise<Record<string, unknown>[]>,
 * policies(): Promise<Record<string, unknown>[]>,
 * retryStage(id: number, stage: Record<string, unknown>): Promise<boolean>,
 * requeuePolicy(policy: Record<string, unknown>): Promise<boolean>
 * } }} options
 */
export async function retryFailedAdoBuildStages(parsedPr, snapshot, { workspaceRoot = process.cwd(), exec = defaultExec, stageOnly = false, transport } = {}) {
    if (parsedPr?.provider !== "ado") return false;
    const prBuilds = Array.isArray(snapshot?.prBuilds) ? snapshot.prBuilds : [];
    const failedBuildIds = prBuilds
        .filter((b) => !/PR-Reviewer|AI PR Review/i.test(String(b?.definition || "")) &&
            ((Array.isArray(b?.failedTasks) && b.failedTasks.length > 0) || String(b?.result || "").toLowerCase() === "failed"))
        .map((b) => Number(b.id))
        .filter(positiveInt);
    if (failedBuildIds.length === 0) return false;

    const api = transport || adoObserverTransport(parsedPr, workspaceRoot, exec);
    const rethrow = (error) => {
        if (transport || ["AbortError", "ConnectionError", "ResponseError"].includes(error?.name)) throw error;
    };
    let retried = false;
    let unconfirmed = false;
    for (const id of failedBuildIds) {
        let stageRetried = false;
        let stageFailed = false;
        try {
            const records = await api.timeline(id);
            if (!Array.isArray(records)) throw new Error("ADO omitted the build timeline.");
            const stages = records.filter((stage) => stage.type === "Stage" && ["failed", "canceled", "cancelled"].includes(stage.result));
            for (const stage of stages) {
                if (!nonempty(stage.identifier)) { stageFailed = true; continue; }
                try {
                    if (await api.retryStage(id, stage) === true) { retried = true; stageRetried = true; }
                    else stageFailed = true;
                } catch (error) { rethrow(error); stageFailed = true; }
            }
        } catch (error) { rethrow(error); stageFailed = true; }
        if (!stageRetried || stageFailed) unconfirmed = true;
        if (!stageRetried && !stageOnly) {
            try {
                const policies = await api.policies();
                for (const policy of policies.filter((policy) => record(policy.context) && Number(policy.context.buildId) === id)) {
                    if (await api.requeuePolicy(policy) === true) retried = true;
                }
            } catch (error) { rethrow(error); }
        }
    }
    return retried && (!stageOnly || !unconfirmed);
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

function isHumanApprovalCheck(check) {
    return /Missing Approvals/i.test(String(check?.description || "")) ||
        /minimum.*reviewer|require.*reviewer|merge strategy|proof of presence|comment requirements/i.test(String(check?.name || ""));
}

function isBuildValidationPolicy(policy) {
    return Boolean(policy.buildId) ||
        String(policy.typeId || "").toLowerCase() === "0609b952-1397-4640-95ec-e00a01b2c241" ||
        /^(?:build|build validation)$/i.test(String(policy.name || "").trim());
}

function hasPendingRepairFeedback(snapshot) {
    return !snapshot?.snapshotHeadChanged &&
        (/dirty|conflict/i.test(String(snapshot?.mergeStateStatus || "")) ||
            hasActionableReviewOrCommentFeedback(snapshot));
}

export function scopeAdoSnapshot(snapshot) {
    if (!record(snapshot)) return snapshot;
    if (snapshot.provider !== "ado") {
        return Array.isArray(snapshot.statusCheckRollup)
            ? { ...snapshot, statusCheckRollup: deduplicateStatusCheckRollup(snapshot.statusCheckRollup) }
            : snapshot;
    }
    const sameHead = (head) => nonempty(head) && nonempty(snapshot.headRefOid) && head.toLowerCase() === snapshot.headRefOid.toLowerCase();
    const currentBuilds = (snapshot.prBuilds || []).filter((build) => sameHead(build.sourceHeadSha));
    const policies = (snapshot.policyEvaluations || []).map((policy) => {
        if (!policy.buildId) return policy;
        const linked = currentBuilds.find((build) => Number(build.id) === Number(policy.buildId));
        return {
            ...policy,
            buildIsNotCurrent: policy.buildIsNotCurrent === true ||
                !sameHead(policy.buildSourceHeadSha || linked?.sourceHeadSha),
        };
    });
    const checks = deduplicateStatusCheckRollup(snapshot.statusCheckRollup || []).map((check) => {
        const currentValidation = currentBuilds.filter((build) =>
            !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")));
        const activeValidation = currentValidation.some((build) =>
            ["inprogress", "notstarted", "cancelling", "postponed"].includes(String(build.status || "").toLowerCase()));
        const unboundRequiredValidation = policies.some((policy) =>
            isBuildValidationPolicy(policy) && policy.blocking && (!policy.buildId || policy.buildIsNotCurrent));
        if (String(check.context?.name || check.name || "").toLowerCase() === "codecoverage" &&
            ["failed", "failure", "error"].includes(String(check.state || check.conclusion || "").toLowerCase()) &&
            (activeValidation || unboundRequiredValidation) && !currentValidation.some((build) => (build.failedTasks || []).length)) {
            return { ...check, state: "pending", conclusion: null, description: "Current source-head validation has no final result; coverage cannot be confirmed yet." };
        }
        return check;
    });
    return { ...snapshot, prBuilds: currentBuilds, policyEvaluations: policies, statusCheckRollup: checks };
}

export function ciEvidenceFromSnapshot(snapshot) {
    snapshot = scopeAdoSnapshot(snapshot);
    const evidence = [];
    if (snapshot?.snapshotHeadChanged) return {
        status: "pending", headSha: snapshot.headRefOid || null,
        evidence: ["PR source or merge commit changed during the snapshot; fresh current-head validation must be observed."],
    };
    const builds = (snapshot?.prBuilds || []).filter((build) => !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")));
    const checks = (snapshot?.statusCheckRollup || []).filter((check) => !isHumanApprovalCheck(check));
    let failed = false;
    let pending = false;
    let unknown = !snapshot?.headRefOid || (builds.length === 0 && checks.length === 0) ||
        (snapshot?.provider === "ado" && builds.length === 0);
    for (const build of builds) {
        const status = String(build.status || "").toLowerCase();
        const result = String(build.result || "").toLowerCase();
        evidence.push(`Build ${build.id || "unknown"} ${build.definition || ""}: ${status}/${result || "pending"}`);
        evidence.push(...(build.infrastructureIssues || []));
        evidence.push(...(build.diagnosticErrors || []));
        if (status !== "completed") pending = true;
        else if (result !== "succeeded") failed = true;
        for (const task of build.failedTasks || []) {
            failed = true;
            evidence.push(`Failed task: ${task.name}`, ...(task.issues || []), ...(task.excerpt || []));
            for (const test of task.failedTests || []) evidence.push(`${test.testName}: ${test.errorMessage}`);
        }
    }
    for (const check of checks) {
        const state = String(check.conclusion || check.state || check.status || "").toLowerCase();
        evidence.push(`${check.context?.name || check.name || "Check"}: ${state}; ${check.description || ""}`);
        if (["failure", "failed", "error", "timed_out", "cancelled", "action_required"].includes(state)) failed = true;
        else if (["pending", "queued", "in_progress", "inprogress", "notstarted"].includes(state)) pending = true;
        else if (!["success", "succeeded", "neutral", "skipped", "notapplicable"].includes(state)) unknown = true;
    }
    for (const policy of snapshot?.policyEvaluations || []) {
        const currentBuild = builds.find((build) => Number(build.id) === Number(policy.buildId));
        if (policy.blocking && isBuildValidationPolicy(policy) && !policy.buildId) {
            pending = true;
            evidence.push(`${policy.name}: required build validation has no build result for the current PR head.`);
        } else if (policy.buildId && policy.blocking && policy.buildIsNotCurrent) {
            pending = true;
            evidence.push(`${policy.name}: build validation is stale for the current PR head.`);
        } else if (policy.buildId && policy.blocking && policy.status === "rejected" &&
            (snapshot.provider !== "ado" || (currentBuild?.status === "completed" &&
                ["failed", "canceled", "cancelled", "partiallysucceeded"].includes(String(currentBuild.result || "").toLowerCase())))) {
            failed = true;
            evidence.push(`${policy.name}: current build policy rejected.`);
        } else if (policy.buildId && policy.blocking && policy.status !== "approved") {
            pending = true;
            evidence.push(`${policy.name}: policy validation is ${policy.status || "unknown"}; not a current-head source failure.`);
        }
    }
    evidence.push(...(snapshot?.diagnosticErrors || []));
    return {
        status: failed ? "failed" : pending ? "pending" : unknown ? "unknown" : "passed",
        headSha: snapshot?.headRefOid || null,
        evidence: evidence.slice(0, 60),
    };
}

export function ciWaitingSummary(snapshot, expectedHeadSha = null) {
    if (expectedHeadSha && snapshot.headRefOid !== expectedHeadSha) {
        return `Waiting for the CI provider to recognize pushed commit ${expectedHeadSha}.`;
    }
    snapshot = scopeAdoSnapshot(snapshot);
    if (snapshot?.snapshotHeadChanged) return "The PR head changed; waiting for fresh CI results.";
    const states = [
        ...(snapshot?.prBuilds || []).filter((build) => !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")))
            .map((build) => build.status),
        ...(snapshot?.statusCheckRollup || []).filter((check) => !isHumanApprovalCheck(check))
            .map((check) => check.conclusion || check.state || check.status),
    ].map((state) => String(state || "").toLowerCase());
    const running = states.some((state) => ["inprogress", "in_progress"].includes(state));
    const queued = states.some((state) => ["queued", "notstarted", "postponed"].includes(state));
    if (running && queued) return "CI has running and queued checks.";
    if (running) return "CI checks are running.";
    if (queued) return "CI checks are queued.";
    return "Waiting for CI results for the current PR head.";
}

export function infrastructureBlockersFromSnapshot(snapshot) {
    snapshot = scopeAdoSnapshot(snapshot);
    if (snapshot?.snapshotHeadChanged) return [];
    const builds = (snapshot?.prBuilds || []).filter((build) => !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")));
    if (builds.some((build) => (build.failedTasks || []).length > 0)) return [];
    return builds.flatMap((build) => (build.infrastructureIssues || []).filter((issue) =>
        /brownout|provider.*unable.*process|hosted.*image.*unavailable|image.*retired/i.test(issue)));
}

function infrastructureHandoff(snapshot, prUrl, iterations) {
    const evidence = infrastructureBlockersFromSnapshot(snapshot);
    return evidence.length ? {
        status: "needs-owner-decision", blockerKind: "ci-infrastructure", prUrl, iterations, evidence,
        summary: "Hosted CI provider/image is unavailable; source/test edits cannot resolve this run. Retry only after provider recovery or an approved pipeline-owner change.",
    } : null;
}

export function dependencyBlockersFromSnapshot(snapshot) {
    snapshot = scopeAdoSnapshot(snapshot);
    if (snapshot?.snapshotHeadChanged) return [];
    const tasks = (snapshot?.prBuilds || []).filter((build) => !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")))
        .flatMap((build) => build.failedTasks || []);
    const text = tasks.flatMap((task) => [...(task.issues || []), ...(task.excerpt || [])]).join("\n");
    if (tasks.some((task) => (task.failedTests || []).length > 0) || isDefinitiveCodeOrTestFailure(text)) return [];
    if (/\b(?:401|403|502|503|504)\b|service unavailable|unauthorized|forbidden|(?:plugin|artifact|dependency).*(?:could not.*resolv|could not.*find|not found|could not be resolved)|could not resolve all files for configuration/is.test(text) &&
        /(?:pkgs\.visualstudio\.com|_packaging\/|AuthApp.*maven|maven.*AuthApp)/i.test(text)) {
        const lines = tasks.flatMap((task) => [...(task.issues || []), ...(task.excerpt || [])]);
        const causes = lines.filter((line) => /UnknownHostException|\b(?:401|403|502|503|504)\b|service unavailable|unauthoriz|forbidden|Plugin \[|could not resolve|was not found/i.test(line));
        return [...new Set([...causes, ...lines])].slice(0, 48);
    }
    return [];
}

function environmentHandoff(snapshot, prUrl, iterations) {
    const infrastructure = infrastructureHandoff(snapshot, prUrl, iterations);
    if (infrastructure) return infrastructure;
    const evidence = dependencyBlockersFromSnapshot(snapshot);
    return evidence.length ? {
        status: "needs-owner-decision", blockerKind: "ci-dependencies", prUrl, iterations,
        summary: "Current validation failed during internal feed/plugin resolution before source verification. Investigate feed access, artifact availability and pipeline configuration; no source/test defect is established.",
        evidence,
    } : null;
}

function hasTaskDiagnostics(task) {
    return [...(task.issues || []), ...(task.excerpt || []),
        ...(task.failedTests || []).flatMap((test) => [test.errorMessage, test.testName])].some(nonempty);
}

function checkoutTransportFailures(snapshot) {
    const validationBuilds = (snapshot?.prBuilds || []).filter((build) =>
        !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")));
    const builds = validationBuilds.filter((build) =>
        ["failed", "partiallysucceeded", "canceled", "cancelled"].includes(String(build.result || "").toLowerCase()));
    const tasks = validationBuilds.flatMap((build) => build.failedTasks || []);
    if (!builds.length || !tasks.length || !nonempty(snapshot?.headRefOid) ||
        validationBuilds.some((build) => (build.diagnosticErrors || []).length) ||
        builds.some((build) => !positiveInt(Number(build.id)) || !(build.failedTasks || []).length)) return null;
    const evidence = tasks.map((task) => [task.name, ...(task.issues || []), ...(task.excerpt || [])].join("\n"));
    if (tasks.some((task) => (task.failedTests || []).length) ||
        isDefinitiveCodeOrTestFailure(evidence.join("\n")) ||
        !evidence.every((text, index) =>
            (/checkout|get sources/i.test(String(tasks[index].name || "")) || /git fetch|git clone|fetch-pack/i.test(text)) &&
            /Connection reset|ECONNRESET|ETIMEDOUT|Could not resolve host|unable to access.*(?:timeout|timed out)|curl.*(?:56|28)/i.test(text))) return null;
    return { builds, evidence };
}

/** @param {{policyEvaluations?: unknown[], prBuilds?: unknown[]}} snapshot
 * @returns {Record<string, unknown>[]} */
export function staleAdoBuildPolicies(snapshot) {
    return (snapshot.policyEvaluations || []).filter((policy) =>
        policy.blocking && policy.buildId && policy.buildIsNotCurrent &&
        ["approved", "rejected"].includes(policy.status) &&
        !(snapshot.prBuilds || []).some((build) => policy.buildDefinitionId && build.definitionId === policy.buildDefinitionId &&
            ["inprogress", "notstarted", "cancelling", "postponed"].includes(String(build.status || "").toLowerCase())) &&
        /^[a-f0-9-]{36}$/i.test(policy.evaluationId || ""));
}

export function refreshStaleAdoBuildPolicies(parsedPr, snapshot, { workspaceRoot = process.cwd(), exec = defaultExec } = {}) {
    if (parsedPr?.provider !== "ado") return [];
    const stale = staleAdoBuildPolicies(snapshot);
    if (!stale.length) return [];
    const base = `${parsedPr.orgUrl}/${encodeURIComponent(parsedPr.project)}/_apis/policy/evaluations`;
    const script = [
        "$ErrorActionPreference = 'Stop'",
        "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
        "if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Azure DevOps authentication failed for stale-build refresh.' }",
        "$headers = @{ Authorization = \"Bearer $token\"; 'Content-Type' = 'application/json' }",
        ...stale.map((policy) => `Invoke-RestMethod -Method Patch -Uri '${base}/${policy.evaluationId}?api-version=7.1-preview.1' -Headers $headers -Body '{}' | Out-Null`),
    ].join("; ");
    exec("powershell", ["-NoProfile", "-Command", script], { cwd: workspaceRoot });
    return stale.map((policy) => policy.evaluationId);
}

export function renderObserverDescription(parsedPr, snapshot, statusBlock, ci = ciEvidenceFromSnapshot(snapshot)) {
    if (!/^[a-f0-9]{40,64}$/i.test(snapshot?.headRefOid || "")) {
        throw new Error("Cannot publish observer status without a full observed PR commit SHA.");
    }
    const originalBody = snapshot.description ?? snapshot.body;
    if (typeof originalBody !== "string") throw new Error("Cannot publish observer status without the current PR description.");
    let body = upsertMarkerBlock(originalBody, statusMarkers, statusBlock);
    const previousVerification = parseMarkerBlock(originalBody, verificationMarkers) || {
        local: { status: "unverified", commands: [], evidence: ["No independently attested local result."] },
    };
    if (previousVerification.local?.status === "passed" &&
        previousVerification.local.headSha !== snapshot.headRefOid) {
        previousVerification.local.status = "unverified";
    }
    body = body.replace(
        /(## Validation & Testing\r?\n)Local verification(?::| (?:passed\.|failed\.|is incomplete;|has not been confirmed\.|status could not be confirmed\.))[^\r\n]*/,
        (line, heading) => {
            // The compact marker omits local evidence; retain the bounded narrative reason.
            const previousLine = line.slice(heading.length);
            const localSummary = localVerificationSummary({ local: { status: previousVerification.local?.status } });
            const ciSuffix = ["passed", "failed", "pending", "unknown", "not-observed"].flatMap((status) => [
                ciVerificationSummary({ ci: { status, headSha: snapshot.headRefOid } }),
                ciVerificationSummary({ ci: { status } }),
            ]).find((summary) => previousLine.endsWith(` ${summary}`));
            const previousLocal = ciSuffix ? previousLine.slice(0, -ciSuffix.length).trimEnd() : "";
            const preserveReason = ["failed", "deferred"].includes(previousVerification.local?.status) &&
                (previousLocal === localSummary || previousLocal.startsWith(`${localSummary} `));
            const local = preserveReason ? {
                ...previousVerification.local, evidence: [previousLocal.slice(localSummary.length).trim()],
            } : previousVerification.local;
            return heading + verificationSummary({ local, ci });
        },
    );
    body = upsertMarkerBlock(body, verificationMarkers, renderVerificationBlock({ ...previousVerification, ci }));
    const decisions = parseMarkerBlock(originalBody, deliveryDecisionMarkers);
    if (decisions) {
        body = upsertMarkerBlock(body, deliveryDecisionMarkers, renderDeliveryDecisionBlock({
            rolloutTag: decisions.rollout?.tag,
            rolloutJustification: decisions.rollout?.justification,
            testCoverageTag: decisions.testCoverage?.tag,
            testCoverageJustification: `Tests authored/updated; local verification ${previousVerification.local?.status || "unverified"}; current-head CI ${ci.status}. See verification receipt and remaining checks.`,
        }));
    }
    return parsedPr.provider === "ado" ? fitAdoDescription(body) : body;
}

export function publishObserverStatus(parsedPr, snapshot, statusBlock, { workspaceRoot = process.cwd(), exec = defaultExec, ci = ciEvidenceFromSnapshot(snapshot) } = {}) {
    const originalBody = snapshot.description ?? snapshot.body;
    const body = renderObserverDescription(parsedPr, snapshot, statusBlock, ci);
    if (parsedPr.provider === "ado") {
        const base = `${parsedPr.orgUrl}/${encodeURIComponent(parsedPr.project)}/_apis/git/repositories/${encodeURIComponent(parsedPr.repoName)}/pullRequests/${parsedPr.prNumber}?api-version=7.0`;
        const b64 = (value) => Buffer.from(value, "utf8").toString("base64");
        const script = [
            "$ErrorActionPreference = 'Stop'",
            "$token = az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv",
            "if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Azure DevOps authentication failed for status update.' }",
            "$headers = @{ Authorization = \"Bearer $token\"; 'Content-Type' = 'application/json' }",
            `$uri = '${base}'`,
            "$live = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get",
            `$expectedBody = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${b64(originalBody)}'))`,
            `if ($live.status -ne 'active' -or $live.lastMergeSourceCommit.commitId -ne '${snapshot.headRefOid}' -or $live.description -cne $expectedBody) { throw 'PR changed during observer status publication; refusing overwrite.' }`,
            `$payload = [Convert]::FromBase64String('${b64(JSON.stringify({ description: body }))}')`,
            "Invoke-RestMethod -Uri $uri -Headers $headers -Method Patch -Body $payload | Out-Null",
        ].join("; ");
        exec("powershell", ["-NoProfile", "-Command", script], { cwd: workspaceRoot });
    } else {
        const account = resolveGitHubAccount({ hostname: parsedPr.hostname }, workspaceRoot, exec);
        const token = account
            ? exec("gh", ["auth", "token", "--hostname", parsedPr.hostname, "--user", account], {
                cwd: workspaceRoot,
                env: buildGitHubKeyringEnv(),
            })
            : "";
        const overrides = buildGitHubTokenOverrides(parsedPr.hostname, token);
        const env = buildNormalizedProcessEnv(process.env, overrides);
        const endpoint = `repos/${parsedPr.slug}/pulls/${parsedPr.prNumber}`;
        const live = JSON.parse(exec("gh", ["api", "--hostname", parsedPr.hostname, endpoint], { cwd: workspaceRoot, env }));
        if (live.state !== "open" || live.head?.sha !== snapshot.headRefOid || live.body !== originalBody) {
            throw new Error("PR changed during observer status publication; refusing overwrite.");
        }
        exec("gh", ["api", "--hostname", parsedPr.hostname, "--method", "PATCH", endpoint, "-f", `body=${body}`], { cwd: workspaceRoot, env });
    }
    return body;
}

export async function runPrObserver(ctx, dependencies = {}) {
    ctx.signal?.throwIfAborted();
    let snapshot = null;
    let repairAttempts = 0;
    const workspaceRoot = ctx.args?.options?.workspaceRoot || process.cwd();
    const environment = { ...(dependencies.environment || process.env) };
    const trustedRepository = dependencies.observeOnly || dependencies.repairHost ? dependencies.trustedRepository : null;
    if ((dependencies.observeOnly || dependencies.repairHost) && !record(trustedRepository)) {
        throw new Error("Managed observation requires the exact trusted repository target.");
    }
    const parseTarget = (url) => parsePullRequestUrl(url, trustedRepository);
    const approvedJavaHome = ctx.args?.options?.javaHome;
    if (approvedJavaHome !== undefined) {
        if (!nonempty(approvedJavaHome)) throw new Error("options.javaHome must be a non-empty approved JDK installation path.");
        environment.JAVA_HOME = approvedJavaHome;
        const target = parseTarget(ctx.args?.prUrl);
        if (target && !resolveTargetJavaHome(target, environment)) throw new Error("Approved observer JDK does not satisfy the target's toolchain requirements.");
    } else {
        const target = parseTarget(ctx.args?.prUrl);
        const selected = target && resolveTargetJavaHome(target, environment, { allowConfiguredFallback: true });
        if (selected) {
            environment.JAVA_HOME = selected;
            await recordMilestone(ctx, {
                id: `observer-toolchain-${createHash("sha256").update(`${ctx.args?.prUrl}:${selected}`).digest("hex")}`,
                title: "Build toolchain selected",
                outcome: "completed",
                summary: `Using installed compatible JDK at ${selected} for this run. Global JAVA_HOME is unchanged.`,
            });
        }
    }
    const fetch = dependencies.fetchPrSnapshot || ((pr) => fetchPullRequestSnapshot(pr, { workspaceRoot, exec: dependencies.exec || defaultExec, trustedRepository }));
    const result = await runObserverLoop(ctx, {
        ...dependencies,
        environment,
        onRepairAttempt: () => { repairAttempts++; dependencies.onRepairAttempt?.(); },
        fetchPrSnapshot: async (pr) => { snapshot = scopeAdoSnapshot(await fetch(pr)); return snapshot; },
    });
    ctx.signal?.throwIfAborted();
    if (!snapshot) return result;
    const ci = ciEvidenceFromSnapshot(snapshot);
    if (result.expectedHeadSha && snapshot.headRefOid !== result.expectedHeadSha) {
        ci.status = "pending";
        ci.evidence.push(`Awaiting validation of pushed head ${result.expectedHeadSha}; provider reports ${snapshot.headRefOid}.`);
    }
    if (result.blockerKind === "pr-identity") {
        ci.status = "unknown";
        ci.headSha = null;
        ci.evidence = ["Pull request identity changed; CI for the reviewed head is unconfirmed."];
    }
    result.ci = ci;
    result.repairAttempts = repairAttempts;
    result.headSha = snapshot.headRefOid || null;
    result.evidence = [...new Set([...(result.evidence || []), ...ci.evidence])];
    result.remainingChecks = (snapshot.statusCheckRollup || []).filter((check) =>
        !["success", "succeeded", "neutral", "skipped", "notapplicable"].includes(String(check.conclusion || check.state || "").toLowerCase()));
    result.remainingPolicies = (snapshot.policyEvaluations || []).filter((policy) => policy.blocking && policy.status !== "approved");
    if (result.status === currentStatuses.readyForHumanMerge && ci.status !== "passed") {
        result.status = ci.status === "failed" ? currentStatuses.ciFailed : currentStatuses.waitingForCi;
        result.summary = ciVerificationSummary({ ci });
    }
    if (dependencies.observeOnly && ci.status === "failed") {
        result.status = currentStatuses.ciFailed;
    }
    if (ci.status === "passed" && result.remainingPolicies.length && result.status === currentStatuses.readyForHumanMerge) {
        result.status = currentStatuses.awaitingHumanReview;
        result.summary = "Current-head CI passed; required human review/policy actions remain.";
    }
    const waiting = [currentStatuses.waitingForCi, currentStatuses.observationExhausted].includes(result.status);
    result.continuation = waiting && !dependencies.observeOnly && !dependencies.repairHost ? {
        workflow: "pr-observer",
        args: {
            ...ctx.args,
            maxIterations: Math.max(0, repairLimit(ctx.args) - repairAttempts),
            options: { ...(ctx.args.options || {}), workspaceRoot },
        },
        reason: result.summary,
    } : null;
    const nextAction = waiting && (dependencies.observeOnly || dependencies.repairHost)
        ? dependencies.repairHost ? "Continue through the owning workflow with its recorded remaining repair allowance."
            : "Continue read-only observation through the owning workflow; no repair or publication is authorized."
        : observerNextAction(result);
    result.statusBlock = renderCurrentStatusBlock({
        status: result.status,
        owner: waiting ? currentStatusOwners.observer : currentStatusOwners.human,
        runId: ctx.runId || "local",
        repository: parseTarget(ctx.args?.prUrl)?.repository || "",
        prUrl: ctx.args.prUrl,
        summary: result.summary,
        nextAction,
    });
    const publish = dependencies.publishStatus || publishObserverStatus;
    if (!dependencies.observeOnly && ctx.args?.options?.publishStatus !== false) {
        try {
            await publish(parseTarget(ctx.args.prUrl), snapshot, result.statusBlock, { workspaceRoot, exec: dependencies.exec || defaultExec, ci });
            result.statusPublication = { status: "updated" };
        } catch (error) {
            if (ctx.signal?.aborted || ["AbortError", "ConnectionError", "ResponseError"].includes(error?.name)) throw error;
            result.statusPublication = { status: "failed", error: error.message };
            result.evidence.push(`PR status publication failed: ${error.message}`);
            ctx.log?.(`PR status publication failed: ${error.message}`);
        }
    }
    await recordMilestone(ctx, {
        id: `observer-outcome-${createHash("sha256").update(JSON.stringify([ctx.args.prUrl, result.headSha, result.status, result.ci.status])).digest("hex")}`,
        title: "PR observation",
        outcome: result.statusPublication?.status === "failed" ? "blocked" : result.ci.status === "passed" &&
            ["ready-for-human-merge", "awaiting-human-review"].includes(result.status) ? "completed" :
            result.status === "waiting-for-ci" ? "deferred" : "blocked",
        summary: `${result.summary || deliveryStatusSummary(result.status)} ${result.statusPublication?.status === "failed" ? observerNextAction(result) : nextAction}${result.statusPublication?.status === "failed" ? ` PR status update failed: ${result.statusPublication.error}; its description may be stale.` : ""}`,
        prUrl: result.prUrl,
    });
    return result;
}

async function runObserverLoop(ctx, dependencies = {}) {
    const args = record(ctx?.args) ? ctx.args : {};
    const rawOptions = record(args.options) ? args.options : {};
    const repairHost = dependencies.repairHost;
    const parsedPr = parsePullRequestUrl(args.prUrl, dependencies.observeOnly || repairHost ? dependencies.trustedRepository : null);
    if (!parsedPr) {
        return {
            status: "needs-owner-decision",
            prUrl: args.prUrl || null,
            summary: "Supply a valid GitHub/GHE (https://<host>/<owner>/<repo>/pull/<number>) or Azure DevOps (https://<org>.visualstudio.com/<project>/_git/<repo>/pullrequest/<number>) pull request URL.",
            evidence: [],
            iterations: 0,
        };
    }

    const maxIterations = dependencies.observeOnly ? 0 : repairLimit(args);
    const workspaceRoot = nonempty(rawOptions.workspaceRoot) ? rawOptions.workspaceRoot : process.cwd();
    const exec = dependencies.exec || defaultExec;
    const fetchSnapshot = dependencies.fetchPrSnapshot || ((pr) => fetchPullRequestSnapshot(pr, { workspaceRoot, exec }));
    const sleep = async (ms) => {
        ctx.signal?.throwIfAborted();
        if (dependencies.sleep) await dependencies.sleep(ms);
        else await delay(ms, undefined, { signal: ctx.signal });
        ctx.signal?.throwIfAborted();
    };
    const ciPollIntervalMs = Number.isFinite(rawOptions.ciPollIntervalMs) && rawOptions.ciPollIntervalMs >= 0
        ? Number(rawOptions.ciPollIntervalMs)
        : DEFAULT_CI_POLL_INTERVAL_MS;
    const maxCiPollAttempts = Number.isSafeInteger(rawOptions.maxCiPollAttempts) && rawOptions.maxCiPollAttempts >= 0
        ? Number(rawOptions.maxCiPollAttempts)
        : DEFAULT_MAX_CI_POLL_ATTEMPTS;
    const pushRepairs = !dependencies.observeOnly && rawOptions.pushRepairs !== false;
    const pauseOnBlocker = rawOptions.pauseOnBlocker !== undefined
        ? rawOptions.pauseOnBlocker === true && typeof ctx?.pause === "function"
        : typeof ctx?.pause === "function";

    const prefix = `pr-observer-v2-${createHash("sha256").update(parsedPr.prUrl).digest("hex").slice(0, 16)}`;
    const step = (key, producer, options) => ctx.step(`${prefix}-${key}`, producer, options);
    const phase = (title) => ctx.phase?.(title);
    const log = (message) => ctx.log?.(message);

    const target = {
        id: `${parsedPr.repository}-pr-${parsedPr.prNumber}`,
        repository: parsedPr.repository,
        localPath: parsedPr.localPath,
        hostname: parsedPr.hostname,
        slug: parsedPr.slug,
        baseBranch: dependencies.trustedRepository?.baseBranch || "dev",
        account: dependencies.trustedRepository?.account || "",
        verificationCommands: Array.isArray(args.verificationCommands) ? args.verificationCommands : [],
    };
    let repoRoot = path.resolve(workspaceRoot, target.localPath || ".");
    const selectedJavaHome = resolveTargetJavaHome(target, dependencies.environment || process.env);
    const builderGuides = repairHost?.guides?.builder || resolveRoleSkillGuides("builder", [target]);
    const reviewerGuides = repairHost?.guides?.reviewer || resolveRoleSkillGuides("reviewer", [target]);
    const attemptedRepairIdentities = new Set();
    const retriedCheckoutHeads = new Set();
    let expectedHeadSha = repairHost ? repairHost.expectedHead() : null;
    const snapshotProblem = (value) => {
        if (!dependencies.observeOnly && !repairHost) return null;
        if (!["open", "active"].includes(String(value?.state || "").toLowerCase())) {
            return "The pull request is not open; inspect its current state before continuing.";
        }
        if (Number(value?.number) !== parsedPr.prNumber ||
            (dependencies.trustedRepository.baseBranch && value?.baseRefName !== dependencies.trustedRepository.baseBranch) ||
            (repairHost ? !repairHost.acceptedHead(value?.headRefOid)
                : dependencies.trustedRepository.headSha && value?.headRefOid !== dependencies.trustedRepository.headSha) ||
            (dependencies.trustedRepository.headBranch && value?.headRefName !== dependencies.trustedRepository.headBranch)) {
            return "The pull request identity, base, or reviewed head changed; inspect it before continuing.";
        }
        if (value?.url) {
            const snapshotPr = parsePullRequestUrl(value.url, dependencies.trustedRepository);
            if (!snapshotPr || snapshotPr.prNumber !== parsedPr.prNumber) {
                return "The observed pull request URL does not match the approved target.";
            }
        }
        return null;
    };
    const unresolvedOrUnknownReview = (value) => (dependencies.observeOnly || repairHost) && (
        (parsedPr.provider !== "ado" && value?.reviewThreadsComplete !== true) ||
        Number(value?.unresolvedReviewThreadCount || 0) > 0 ||
        (Array.isArray(value?.activeReviewThreads) && value.activeReviewThreads.length > 0)
    );

    const firstIteration = repairHost ? repairHost.firstIteration : 0;
    if (!Number.isSafeInteger(firstIteration) || firstIteration < 0 || firstIteration > maxIterations) {
        throw new Error("Invalid persisted observer repair position.");
    }
    for (let iteration = firstIteration; iteration <= maxIterations; iteration++) {
        phase("Observe");
        log(`Observing ${parsedPr.prUrl} (iteration ${Math.min(iteration + 1, maxIterations)}/${maxIterations})`);

        let snapshot = await step(`snapshot-${iteration}`, async () => fetchSnapshot(parsedPr), { volatile: true });
        let problem = snapshotProblem(snapshot);
        if (problem) {
            return { status: "needs-owner-decision", blockerKind: "pr-identity", prUrl: parsedPr.prUrl,
                summary: problem, evidence: [], iterations: 0 };
        }
        let totalPollsUsed = 0;
        let pollSeq = 0;
        let buildResetCount = 0;
        const getBuildIdsKey = (s) => Array.isArray(s?.prBuilds) ? s.prBuilds.map((b) => b?.id || 0).join(",") : "";
        let activeBuildIdsKey = getBuildIdsKey(snapshot);
        let observation = null;
        let afterObserveSnap = snapshotWorktree(repoRoot, exec);
        let lastWaitingFingerprint = null;
        const unchangedWaiting = () => nonempty(lastWaitingFingerprint) &&
            classificationFingerprint(snapshot) === lastWaitingFingerprint;

        for (let ciWaitRound = 0; ciWaitRound <= maxCiPollAttempts; ciWaitRound++) {
            if (!expectedHeadSha || snapshot.headRefOid === expectedHeadSha) {
                const blocked = environmentHandoff(snapshot, parsedPr.prUrl, iteration);
                if (blocked) return blocked;
            }
            if (parsedPr.provider === "ado" && !dependencies.observeOnly && (!repairHost || dependencies.refreshStaleBuilds)) {
                const staleKey = createHash("sha256").update(JSON.stringify([
                    snapshot.headRefOid, staleAdoBuildPolicies(snapshot).map((policy) => [policy.evaluationId, policy.buildId]),
                ])).digest("hex").slice(0, 16);
                const refreshed = await step(`refresh-stale-build-${staleKey}`, () =>
                    (dependencies.refreshStaleBuilds || refreshStaleAdoBuildPolicies)(parsedPr, snapshot, { workspaceRoot, exec }));
                if (refreshed.length) {
                    for (const policy of snapshot.policyEvaluations || []) {
                        if (refreshed.includes(policy.evaluationId)) policy.status = "queued";
                    }
                    log(`Requested fresh current-head build validation for ${refreshed.length} stale required policy/policies.`);
                }
            }
            while (
                totalPollsUsed < maxCiPollAttempts &&
                ((expectedHeadSha && snapshot.headRefOid !== expectedHeadSha) ||
                    unchangedWaiting() ||
                    (!hasPendingRepairFeedback(snapshot) &&
                        (ciEvidenceFromSnapshot(snapshot).status === "pending" || isSnapshotWaitingForCiOnly(snapshot))))
            ) {
                await recordMilestone(ctx, {
                    id: `${prefix}-ci-wait-${createHash("sha256").update(String(expectedHeadSha || snapshot.headRefOid || "unknown")).digest("hex")}`,
                    title: "Waiting for CI",
                    outcome: "deferred",
                    summary: `${ciWaitingSummary(snapshot, expectedHeadSha)} The observer is waiting for remote results.`,
                    prUrl: parsedPr.prUrl,
                });
                totalPollsUsed++;
                pollSeq++;
                log(`${ciWaitingSummary(snapshot, expectedHeadSha)} Checking again in ${Math.round(ciPollIntervalMs / 1000)}s (${totalPollsUsed}/${maxCiPollAttempts}).`);
                await sleep(ciPollIntervalMs);
                snapshot = await step(
                    `snapshot-${iteration}-poll-${pollSeq}`,
                    async () => fetchSnapshot(parsedPr),
                    { volatile: true },
                );
                problem = snapshotProblem(snapshot);
                if (problem) return { status: "needs-owner-decision", blockerKind: "pr-identity", prUrl: parsedPr.prUrl, summary: problem, evidence: [], iterations: iteration };
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

            if (expectedHeadSha && snapshot.headRefOid !== expectedHeadSha) {
                return {
                    status: "waiting-for-ci",
                    prUrl: parsedPr.prUrl,
                    expectedHeadSha,
                    summary: `Waiting for provider to observe pushed head ${expectedHeadSha}.`,
                    evidence: [`Provider head: ${snapshot.headRefOid || "unknown"}`],
                    iterations: iteration,
                };
            }

            const blocked = environmentHandoff(snapshot, parsedPr.prUrl, iteration);
            if (blocked) return blocked;

            // Reaching the wait bound does not require classifying the same evidence again.
            if (unchangedWaiting()) break;

            if (!hasPendingRepairFeedback(snapshot) &&
                (ciEvidenceFromSnapshot(snapshot).status === "pending" || isSnapshotWaitingForCiOnly(snapshot))) {
                const runningBuilds = (Array.isArray(snapshot.prBuilds) ? snapshot.prBuilds : [])
                    .filter((b) => ["inprogress", "notstarted", "postponed"].includes(String(b?.status || "").toLowerCase()))
                    .map((b) => `${b.definition || "CI"} (${b.buildNumber || b.id || "identifier unavailable"}): ${b.status}`);
                observation = {
                    verdict: "waiting-for-ci",
                    repairKind: "none",
                    summary: ciWaitingSummary(snapshot),
                    evidence: runningBuilds.length > 0 ? runningBuilds : ciEvidenceFromSnapshot(snapshot).evidence,
                    actionableFindings: [],
                };
                break;
            }

            if (isSnapshotReadyForHumanMerge(snapshot) && !unresolvedOrUnknownReview(snapshot)) {
                const passedBuilds = (Array.isArray(snapshot.prBuilds) ? snapshot.prBuilds : [])
                    .map((b) => `${b.definition || "build"} (${b.buildNumber || b.id}): ${b.result || "succeeded"}`);
                observation = {
                    verdict: "ready",
                    repairKind: "none",
                    summary: `All required CI checks passed on PR #${parsedPr.prNumber} with no merge conflicts or unresolved review threads.`,
                    evidence: [
                        `mergeStateStatus: ${snapshot.mergeStateStatus}`,
                        ...passedBuilds,
                        "activeReviewThreads: 0 unresolved",
                    ],
                    actionableFindings: [],
                };
                break;
            }

            const failedTasks = (snapshot?.prBuilds || [])
                .filter((build) => !/PR-Reviewer|AI PR Review/i.test(String(build.definition || "")))
                .flatMap((build) => build.failedTasks || []).filter(hasTaskDiagnostics);
            const transport = parsedPr.provider === "ado" && checkoutTransportFailures(snapshot);
            if (transport) {
                ctx.signal?.throwIfAborted();
                const handoff = (reason) => ({
                    status: "needs-owner-decision", blockerKind: "ci-infrastructure", prUrl: parsedPr.prUrl,
                    summary: `${reason} No source repair was dispatched; the pipeline owner must inspect checkout connectivity.`,
                    evidence: transport.evidence, iterations: iteration,
                });
                if (!pushRepairs || (repairHost && !dependencies.retryFailedBuilds) || totalPollsUsed >= maxCiPollAttempts) {
                    return handoff("CI checkout failed before compilation/tests; automatic retry or observation was not enabled.");
                }
                if (retriedCheckoutHeads.has(snapshot.headRefOid)) {
                    return handoff("CI checkout is still failing after the single transport retry for this source head.");
                }
                const retryKey = createHash("sha256").update(snapshot.headRefOid).digest("hex").slice(0, 16);
                const buildIds = transport.builds.map((build) => Number(build.id)).sort((a, b) => a - b);
                const receipt = await step(`retry-checkout-${retryKey}`, async () => {
                    ctx.signal?.throwIfAborted();
                    const retried = await (dependencies.retryFailedBuilds || retryFailedAdoBuildStages)(parsedPr, {
                        ...snapshot, prBuilds: transport.builds,
                    }, { workspaceRoot, exec, stageOnly: true });
                    return { retried: retried === true, buildIds };
                });
                ctx.signal?.throwIfAborted();
                if (receipt?.retried !== true) return handoff("CI checkout retry could not be confirmed.");
                if (JSON.stringify(receipt.buildIds) !== JSON.stringify(buildIds)) {
                    return handoff("CI checkout failed in another build after the single transport retry for this source head.");
                }
                retriedCheckoutHeads.add(snapshot.headRefOid);
                await recordMilestone(ctx, {
                    id: `${prefix}-checkout-retry-${retryKey}`,
                    title: "CI checkout retry requested",
                    outcome: "deferred",
                    summary: "CI checkout failed before compilation/tests. A remote retry was requested; the source repair allowance is unchanged.",
                    prUrl: parsedPr.prUrl,
                });
                totalPollsUsed++;
                pollSeq++;
                await sleep(ciPollIntervalMs);
                ctx.signal?.throwIfAborted();
                snapshot = await step(`snapshot-${iteration}-checkout-retry-${pollSeq}`,
                    async () => fetchSnapshot(parsedPr), { volatile: true });
                problem = snapshotProblem(snapshot);
                if (problem) return { status: "needs-owner-decision", blockerKind: "pr-identity", prUrl: parsedPr.prUrl, summary: problem, evidence: [], iterations: iteration };
                observation = {
                    verdict: "waiting-for-ci", repairKind: "none",
                    summary: "CI checkout retry was requested; current-head validation still needs observation.",
                    evidence: transport.evidence, actionableFindings: [],
                };
                continue;
            }
            if (failedTasks.length > 0) {
                observation = {
                    verdict: "repair",
                    repairKind: "ci-failure",
                    summary: "Current PR validation reports concrete failed tasks.",
                    evidence: ciEvidenceFromSnapshot(snapshot).evidence,
                    actionableFindings: failedTasks.map((task) => [
                        task.name, ...(task.issues || []), ...(task.excerpt || []),
                        ...(task.failedTests || []).map((test) => `${test.testName}: ${test.errorMessage}`),
                    ].join("\n")),
                };
                break;
            }
            if (ciEvidenceFromSnapshot(snapshot).status === "failed") {
                return {
                    status: "needs-owner-decision", blockerKind: "ci-diagnostics", prUrl: parsedPr.prUrl,
                    summary: "Validation reports failure, but no current-head compiler/test/task diagnostic is available. Inspect the referenced build before assigning source repairs.",
                    evidence: ciEvidenceFromSnapshot(snapshot).evidence,
                    iterations: iteration,
                };
            }
            if (ciEvidenceFromSnapshot(snapshot).status === "passed" &&
                !/dirty|conflict/i.test(String(snapshot.mergeStateStatus || "")) &&
                !hasActionableReviewOrCommentFeedback(snapshot) && !unresolvedOrUnknownReview(snapshot)) {
                return {
                    status: currentStatuses.awaitingHumanReview,
                    prUrl: parsedPr.prUrl,
                    summary: "Current-head CI passed; repository review/merge policies require human action.",
                    evidence: ciEvidenceFromSnapshot(snapshot).evidence,
                    iterations: iteration,
                };
            }

            if (dependencies.observeOnly) {
                return {
                    status: "needs-owner-decision",
                    prUrl: parsedPr.prUrl,
                    summary: "The PR has unresolved feedback, conflicts, or unverified checks. Human review is required; no repairs were attempted.",
                    evidence: ciEvidenceFromSnapshot(snapshot).evidence,
                    iterations: iteration,
                };
            }

            const currentFingerprint = classificationFingerprint(snapshot);
            const beforeObserveSnap = snapshotWorktree(repoRoot, exec);
            const prChangedFiles = listPrChangedFiles(repoRoot, snapshot, exec);
            const stepKey = ciWaitRound === 0
                ? `observe-${iteration}`
                : `observe-${iteration}-wait-${ciWaitRound}`;
            observation = await step(
                stepKey,
                () => ctx.agent(
                    `Classify the pull request snapshot in a SINGLE turn directly from the supplied DATA (snapshot.prBuilds, snapshot.statusCheckRollup, snapshot.activeReviewThreads, snapshot.inlineReviewComments, snapshot.reviews, snapshot.comments, snapshot.mergeStateStatus, and prChangedFiles).
Do NOT load codebase-researcher skill guides or crawl the repository with broad glob/grep/view searches—root-cause code investigation and fixes belong to code-builder. Only use view on a specific cited file if an inline review comment cannot be classified without reading the cited lines.
Only classify a CI test failure as "needs-owner-decision" for a pre-existing base-branch issue when the snapshot provides explicit evidence that the exact same failure also occurs on the unmodified base branch (${target.baseBranch}, for example in a base-branch coverage job) and is unrelated to any shared utility or contract in prChangedFiles; otherwise classify CI failures as "repair" so code-builder can investigate.\nFresh observation request: ${randomUUID()}\nDATA:\n${JSON.stringify({ pr: parsedPr, prChangedFiles, snapshot, iteration: iteration + 1 })}`,
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

        await recordMilestone(ctx, {
            id: `${prefix}-observed-${iteration}-${computeSnapshotFingerprint(snapshot)}`,
            title: `PR checks inspected, round ${iteration + 1}`,
            outcome: observation.verdict === "ready" ? "completed" :
                observation.verdict === "waiting-for-ci" ? "deferred" : "blocked",
            summary: `${observation.summary} ${observation.verdict === "repair"
                ? (iteration < maxIterations ? "A tactical repair is next." : maxIterations === 0
                    ? "Source repairs are disabled for this run; a developer must review the remaining findings."
                    : "The repair allowance is used up; a developer must review the remaining findings.")
                : ciVerificationSummary({ ci: ciEvidenceFromSnapshot(snapshot) })}`,
            prUrl: parsedPr.prUrl,
        });

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

        if (iteration >= maxIterations) {
            return {
                status: "max-iterations-reached",
                prUrl: parsedPr.prUrl,
                summary: maxIterations === 0
                    ? "Source repairs are disabled for this run. A developer must review the remaining findings."
                    : `The tactical repair allowance is used up after ${maxIterations} repairs; a developer must review the remaining findings.`,
                evidence: observation.evidence || [],
                actionableFindings: observation.actionableFindings || [],
                iterations: maxIterations,
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
        if (iteration >= maxIterations) {
            break;
        }
        phase("Repair");
        if (repairHost) {
            repoRoot = await repairHost.prepare({ iteration, snapshot, observation, parsedPr });
        } else {
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
        }

        const remoteCheck = repairHost ? { ok: true } : verifyCheckoutRemoteMatchesPr(repoRoot, parsedPr, exec);
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

        dependencies.onRepairAttempt?.();
        const repairResult = await step(`repair-${iteration}`, async () => {
            ctx.signal?.throwIfAborted();
            const execution = ctx.agent(
                `${repairHost ? "You are a read-only structured repair author. In files[].content, prefer compact SEARCH/REPLACE blocks (`<<<<<<< SEARCH\\n<exact existing lines>\\n=======\\n<replacement lines>\\n>>>>>>> REPLACE`) for existing modified files instead of re-emitting entire large files (provide full file content only for brand-new or very short files); the application expands them and performs all Git/verification/provider operations. Do not execute commands or mutate files yourself.\n" : ""}Read and follow these repository skill guides: ${builderGuides.join(", ")}.
Apply a surgical tactical fix in ${target.repository} for the reported PR findings (${observation.repairKind}):
${(observation.actionableFindings || []).map((f) => `- ${f}`).join("\n")}
Follow Android Auth PR maintenance rules:
- Never amend or force-push; keep commits incremental/fast-forward.
- Never modify CI pipeline YAML files (.pipelines/*.yml, .github/workflows/*.yml, azure-pipelines/*.yml) during a tactical repair unless the PR itself is a pipeline change.
- When verifying a CI test failure locally, run ONLY the targeted failing test class(es) with --tests "<ClassName>" --max-workers=2 (never run the unfiltered full-repo unitTestReport -Pcoverage task locally on Windows, where unrelated native SQLite/Room tests fail outside the macOS CI runner). Once the targeted test class(es) pass locally, return verdict "pass" so the fix is reviewed and pushed to CI.
${nonempty(selectedJavaHome) ? `- Use JAVA_HOME ${selectedJavaHome} for local verification in this workflow run.` : ""}
- When merging origin/${target.baseBranch}, if a release was cut on ${target.baseBranch}, ensure this PR's CHANGELOG.md / changes.txt entry remains under the active vNext section; if ${target.baseBranch} deleted an adjacent retired flight enum entry, keep the retired flight deleted.
Do not broaden scope or push directly.\nDATA:\n${JSON.stringify({ pr: parsedPr, observation, snapshot, ...(repairHost ? { repositoryRoot: repoRoot } : {}) })}`,
                {
                    label: `Tactical repair (round ${iteration + 1})`,
                    agent: BUILDER_AGENT,
                    schema: repairHost?.builderSchema || slimResultSchema,
                },
            );
            const [result] = await Promise.all([execution, recordMilestone(ctx, {
                id: `${prefix}-repair-${iteration}-started`,
                title: "Tactical repair started",
                outcome: "completed",
                summary: `Tactical repair ${iteration + 1} in progress for the reported CI or review findings.`,
                prUrl: parsedPr.prUrl,
            })]);
            return result;
        });
        if (repairHost && record(repairResult) && repairResult.verdict !== "blocked") {
            await repairHost.apply({ iteration, repairResult, snapshot });
        }

        if (!record(repairResult) || repairResult.verdict === "blocked") {
            const blockerSummary = repairResult?.summary || "Tactical repair blocked.";
            const combinedInfraText = [
                blockerSummary,
                ...(Array.isArray( repairResult?.evidence) ? repairResult.evidence : []),
                ...(Array.isArray(observation?.evidence) ? observation.evidence : []),
            ].join("\n");
            if ((!repairHost || dependencies.retryFailedBuilds) && isTransientCiInfrastructureFailure(combinedInfraText) && iteration + 1 < maxIterations) {
                log(`Detected transient CI infrastructure/network failure on PR #${parsedPr.prNumber}; waiting/retrying CI stage automatically without pausing...`);
                await (dependencies.retryFailedBuilds || retryFailedAdoBuildStages)(parsedPr, snapshot, { workspaceRoot, exec, stageOnly: false, iteration });
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
        await recordMilestone(ctx, {
            id: `${prefix}-repair-${iteration}-authored`,
            title: `Tactical repair ${iteration + 1}`,
            outcome: "completed",
            summary: "Tactical assessment complete; local verification and independent review are next.",
            prUrl: parsedPr.prUrl,
        });

        const revertedProtected = repairHost ? [] : revertUnauthorizedProtectedFiles(repoRoot, {
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
            const gate = await (repairHost?.verify || runLocalVerificationGate)({
                repoRoot,
                target,
                targetImpl: null,
                implementation: null,
                ...(dependencies.exec ? { exec: dependencies.exec } : {}),
                ...(dependencies.environment ? { environment: dependencies.environment } : {}),
            });
            if (gate.errors.length > 0) {
                log(`Local verification failed during tactical repair: ${gate.errors.join("; ")}`);
                if (repairHost) await repairHost.finishAttempt(iteration, "verification-failed");
                continue;
            }
        }

        phase("Verify & Push");
        const reviewContext = repairHost ? await repairHost.reviewContext(iteration) : null;
        const reviewResult = await step(`review-repair-${iteration}`, () => ctx.agent(
            `Read and apply these repository code-review guides: ${reviewerGuides.join(", ")}.
Independently verify that the local tactical code/test repair in the worktree resolves the code/test defects in actionableFindings without regressions.
IMPORTANT: Git commit, git push, PR title/description metadata updates, and remote CI pipeline re-runs (such as Compare Jacoco Coverage or Validate PBI) are executed by the deterministic push-repair step immediately AFTER your review returns verdict "pass". Never return verdict "repair" or raise findings because changes are uncommitted/unpushed in the worktree or because remote CI checks have not re-run yet—return verdict "pass" with findings=[] as soon as the local code and unit-test changes are sound.\nDATA:\n${JSON.stringify({ pr: parsedPr, observation, repairResult, ...(reviewContext ? { reviewContext } : {}) })}`,
            {
                label: `Verify tactical repair (round ${iteration + 1})`,
                agent: REVIEWER_AGENT,
                schema: slimReviewSchema,
            },
        ));
        if (repairHost) await repairHost.verifyReview({ iteration, reviewResult });

        if (!record(reviewResult) || reviewResult.verdict !== "pass" ||
            (Array.isArray(reviewResult.findings) && reviewResult.findings.length > 0)) {
            log(`Tactical repair review requested further changes in iteration ${iteration + 1}.`);
            if (repairHost) await repairHost.finishAttempt(iteration, "review-failed");
            continue;
        }
        await recordMilestone(ctx, {
            id: `${prefix}-repair-${iteration}-reviewed`,
            title: `Tactical repair review ${iteration + 1}`,
            outcome: "completed",
            summary: `Independent review accepted the tactical disposition; ${pushRepairs ? "push and current-head CI are next" : "pushing was not requested"}.`,
            prUrl: parsedPr.prUrl,
        });

        if (pushRepairs) {
            const pushed = await step(`push-repair-${iteration}`, async () => {
                if (repairHost) return repairHost.push({ iteration, snapshot, observation, repairResult, reviewResult });
                const account = resolveGitHubAccount(target, workspaceRoot, exec);
                let resolvedPushToken = "";
                if (nonempty(account)) {
                    try {
                        const token = exec("gh", ["auth", "token", "--user", account, "-h", target.hostname], {
                            cwd: workspaceRoot,
                            env: buildGitHubKeyringEnv(),
                        });
                        if (nonempty(token)) {
                            resolvedPushToken = token;
                        }
                    } catch {
                        // Fall back to ambient auth.
                    }
                }
                const tokenOverrides = buildGitHubTokenOverrides(target.hostname, resolvedPushToken);
                const gitEnv = buildNormalizedProcessEnv(process.env, tokenOverrides);
                assertNoStagedLocalSdkConfiguration(repoRoot, exec);
                const discovered = discoverChangedFilesFromGit(repoRoot, exec, {
                    allowPipelineEdits: false,
                });
                if (discovered.files.length > 0) {
                    for (const file of discovered.files) {
                        if (file.action === "delete") {
                            exec("git", ["rm", "--ignore-unmatch", "--", file.path], { cwd: repoRoot, env: gitEnv });
                        } else {
                            exec("git", ["add", "--", file.path], { cwd: repoRoot, env: gitEnv });
                        }
                    }
                    assertNoStagedLocalSdkConfiguration(repoRoot, exec);
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
                try {
                    pushedSha = exec("git", ["rev-parse", "HEAD"], { cwd: repoRoot, env: gitEnv });
                } catch {
                    pushedSha = "";
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
                return { pushedSha };
            });
            if (nonempty(pushed?.pushedSha)) expectedHeadSha = pushed.pushedSha;
            const unchangedHead = nonempty(pushed?.pushedSha) && pushed.pushedSha === snapshot.headRefOid;
            const changedHead = nonempty(pushed?.pushedSha) && nonempty(snapshot.headRefOid) && !unchangedHead;
            await recordMilestone(ctx, {
                id: `${prefix}-repair-${iteration}-pushed`,
                title: changedHead ? `Tactical repair ${iteration + 1} pushed` : `Tactical push ${iteration + 1} completed`,
                outcome: "completed",
                summary: unchangedHead
                    ? "Push completed; the source head is unchanged. CI observation continues."
                    : changedHead
                        ? `Repair pushed at ${pushed.pushedSha}; waiting for CI results for that commit.`
                        : "Push completed; the resulting source head could not be confirmed. CI observation is next.",
                prUrl: parsedPr.prUrl,
            });
        }
    }

    return {
        status: "max-iterations-reached",
        prUrl: parsedPr.prUrl,
        summary: `Observation stopped after ${maxIterations} repair round(s); a developer must inspect the remaining findings.`,
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
