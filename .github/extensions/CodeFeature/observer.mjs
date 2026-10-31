// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";
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
    ensureIsolatedTargetWorktree,
    loadAgentPromptFromFile,
    pauseForHelp,
    resolveGitHubAccount,
    resolveRoleSkillGuides,
    runLocalVerificationGate,
    slimResultSchema,
    slimReviewSchema,
    snapshotWorktree,
} from "./workflow.mjs";

export const OBSERVER_AGENT = "pr-observer";
export const MAX_OBSERVER_ITERATIONS = 3;

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
    prompt: loadAgentPromptFromFile(
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

export function parsePullRequestUrl(prUrl) {
    if (!nonempty(prUrl)) return null;
    const match = prUrl.trim().match(/^https:\/\/([^/]+)\/([^/]+\/[^/]+)\/pull\/([1-9]\d*)(?:\/.*)?$/i);
    if (!match) return null;
    const [, hostname, slug, numStr] = match;
    const lowerSlug = slug.toLowerCase();
    const repository = lowerSlug.includes("ad-accounts-for-android")
        ? "broker"
        : lowerSlug.includes("microsoft-authentication-library-common-for-android")
            ? "common"
            : lowerSlug.includes("microsoft-authentication-library-for-android")
                ? "msal"
                : lowerSlug.includes("azure-activedirectory-library-for-android")
                    ? "adal"
                    : "android-complete";
    return {
        prUrl: `https://${hostname}/${slug}/pull/${numStr}`,
        hostname,
        slug,
        prNumber: Number(numStr),
        repository,
        localPath: repository === "android-complete" ? "." : repository,
    };
}

export function fetchPullRequestSnapshot(parsedPr, { workspaceRoot = process.cwd(), exec = defaultExec } = {}) {
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
    return JSON.parse(rawJson);
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
            summary: "Supply a valid GitHub or GHE pull request URL (https://<host>/<owner>/<repo>/pull/<number>).",
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

        const snapshot = await step(`snapshot-${iteration}`, async () => fetchSnapshot(parsedPr), { volatile: true });
        if (record(snapshot) && nonempty(snapshot.baseRefName)) {
            target.baseBranch = snapshot.baseRefName;
        }
        const headBranch = record(snapshot) && nonempty(snapshot.headRefName)
            ? snapshot.headRefName
            : `pr-${parsedPr.prNumber}`;

        const beforeObserveSnap = snapshotWorktree(repoRoot, exec);
        const observeGuides = resolveRoleSkillGuides("evidence", [target]);
        const observation = await step(
            `observe-${iteration}`,
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
        const afterObserveSnap = snapshotWorktree(repoRoot, exec);
        if (beforeObserveSnap && afterObserveSnap && beforeObserveSnap !== afterObserveSnap) {
            return {
                status: "needs-owner-decision",
                prUrl: parsedPr.prUrl,
                summary: "Read-only observer unexpectedly modified the worktree.",
                evidence: [],
                iterations: iteration + 1,
            };
        }

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
            const readyStatusBlock = renderCurrentStatusBlock({
                status: currentStatuses.readyForHumanMerge,
                owner: currentStatusOwners.human,
                runId: ctx?.runId || "local",
                repository: parsedPr.repository,
                prUrl: parsedPr.prUrl,
                summary: observation.summary,
                nextAction: "Human review and merge in GitHub/GHE.",
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

        if (observation.verdict === "waiting-for-ci" || observation.verdict === "needs-owner-decision") {
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
            ensureIsolatedTargetWorktree(workspaceRoot, { ...target, baseBranch: headBranch }, exec);
        } catch {
            // Best-effort worktree preparation in mock/test environments.
        }

        const repairResult = await step(`repair-${iteration}`, () => ctx.agent(
            `Read and follow these repository skill guides: ${builderGuides.join(", ")}.
Apply a surgical tactical fix in ${target.repository} for the reported PR findings (${observation.repairKind}):
${(observation.actionableFindings || []).map((f) => `- ${f}`).join("\n")}
Follow Android Auth PR maintenance rules:
- Never amend or force-push; keep commits incremental/fast-forward.
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

        if (target.verificationCommands.length > 0) {
            const gate = runLocalVerificationGate({
                repoRoot,
                target,
                targetImpl: null,
                implementation: null,
                exec,
            });
            if (gate.errors.length > 0) {
                log(`Local verification failed during tactical repair: ${gate.errors.join("; ")}`);
                continue;
            }
        }

        phase("Verify & Push");
        const reviewResult = await step(`review-repair-${iteration}`, () => ctx.agent(
            `Read and apply these repository code-review guides: ${reviewerGuides.join(", ")}.
Independently verify that the tactical repair resolves all actionableFindings without regressions.\nDATA:\n${JSON.stringify({ pr: parsedPr, observation, repairResult })}`,
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
            maxTotalSubagents: 9,
        },
    },
    run: runPrObserver,
};
