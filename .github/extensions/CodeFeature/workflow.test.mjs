// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
    OBSERVER_AGENT,
    computeRepairIdentity,
    hasActionableReviewOrCommentFeedback,
    isSnapshotReadyForHumanMerge,
    isSnapshotWaitingForCiOnly,
    listPrChangedFiles,
    observerCustomAgent,
    observerWorkflowDefinition,
    parsePullRequestUrl,
    runPrObserver,
    selectAddressedReviewThreads,
    verifyCheckoutRemoteMatchesPr,
} from "./observer.mjs";
import {
    currentStatuses,
    deliveryDecisionMarkers,
    parseMarkerBlock,
    statusMarkers,
} from "./status.mjs";
import {
    BUILDER_AGENT,
    EVIDENCE_AGENT,
    MAX_FIX_ITERATIONS,
    REVIEWER_AGENT,
    applyPrerequisiteAnswers,
    applyTargetFiles,
    buildAdoPullRequestPublicationScript,
    buildInspectedFilesContext,
    buildNormalizedProcessEnv,
    capturePreBuilderBaselineBackups,
    collectBuildReceiptOnDisk,
    customAgents,
    discoverChangedFilesFromGit,
    ensureIsolatedTargetWorktree,
    extractRunnableVerificationCommand,
    extractWorkItemIdsFromText,
    formatPrBody,
    formatPrTitle,
    isBuilderShortCircuitedToCi,
    isDefinitiveCodeOrTestFailure,
    isInfrastructureGateFailure,
    isPipelineModificationAuthorized,
    isProtectedPipelineOrQualityPath,
    isSafeRelativeFilePath,
    normalizeEvidenceResponse,
    normalizeReviewResponse,
    normalizeTask,
    parseAdoDesignDocUrl,
    parseAllowedGradleVerificationCommand,
    prerequisiteRecoveryPath,
    renderUnifiedDiff,
    resolveInputSources,
    resolveRoleSkillGuides,
    resolveTargetJavaHome,
    resolveWindowsCmdExecutable,
    restoreTargetFiles,
    revertUnauthorizedProtectedFiles,
    rubric,
    runCodeFeature,
    runLocalVerificationGate,
    runTargetEnvironmentPreflight,
    runWindowsBatchFileSafely,
    slimIntakeSchema,
    slimResultSchema,
    slimReviewSchema,
    snapshotWorktree,
    validateEvidence,
    validateImplementation,
    validateReview,
    validateTask,
    verifyWorkflowPrerequisites,
    workflowDefinition,
} from "./workflow.mjs";

const task = () => ({
    title: "Add server-directed Retry-After throttling",
    revision: "plan-v2",
    workItemId: 3802483,
    designSummary: "Honor eSTS Retry-After headers for silent token requests while serving valid cache hits.",
    approval: {
        approvedRevision: "plan-v2",
        approvedBy: "moghosh@microsoft.com",
        reference: "AB#3802483-approval-1",
    },
    requirements: [
        { id: "R1", text: "Honor valid Retry-After headers on 429/503 responses." },
        { id: "R2", text: "Serve valid cached tokens during active backoff." },
    ],
    targets: [
        {
            id: "common-throttle",
            repository: "common",
            baseBranch: "dev",
            workItemId: 3802484,
            title: "Add Retry-After throttle cache in Common",
            description: "Implement bounded Retry-After cache in common4j.",
            acceptanceCriteria: [
                "Suppresses matching requests until Retry-After expires",
                "Unit tests verify expiry and cache hit behavior",
            ],
            verificationCommands: [],
        },
    ],
    blockingUnknowns: [],
});

const evidence = () => ({
    approvalVerification: {
        verified: true,
        revision: "plan-v2",
        reference: "AB#3802483-approval-1",
        rationale: "Approval record AB#3802483-approval-1 explicitly approves revision plan-v2.",
    },
    references: [
        {
            id: "E1",
            repository: "common",
            revision: "commit-111",
            path: "common4j/src/main/com/microsoft/identity/common/java/controllers/CommandDispatcher.java",
            locator: "CommandDispatcher.submitSilent",
        },
    ],
    facts: [
        {
            statement: "CommandDispatcher dispatches silent token commands after checking the command cache.",
            referenceIds: ["E1"],
        },
    ],
    conflicts: [],
    assumptions: [],
    unknowns: [],
    blockingUnknowns: [],
});

const implementation = () => ({
    taskRevision: "plan-v2",
    targets: [
        {
            targetId: "common-throttle",
            repository: "common",
            summary: "Added RetryAfterThrottleCache in Kotlin with unit tests.",
            files: [
                {
                    path: "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt",
                    action: "create",
                    content: "// Copyright (c) Microsoft Corporation. All rights reserved.\npackage com.microsoft.identity.common.java.throttling\nclass RetryAfterThrottleCache\n",
                    summary: "Bounded Retry-After throttle cache implementation.",
                },
                {
                    path: "common4j/src/test/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCacheTest.kt",
                    action: "create",
                    content: "// Copyright (c) Microsoft Corporation. All rights reserved.\npackage com.microsoft.identity.common.java.throttling\nclass RetryAfterThrottleCacheTest\n",
                    summary: "Unit tests for Retry-After expiry and cache hits.",
                },
            ],
            testsAddedOrUpdated: [
                "RetryAfterThrottleCacheTest.suppressesUntilExpiry",
                "RetryAfterThrottleCacheTest.servesValidCacheHits",
            ],
            verificationCommandsRun: [
                "./gradlew :common4j:test",
            ],
            pr: {
                title: "Add Retry-After throttle cache in Common",
                summary: "Implements bounded server-directed Retry-After caching in common4j.",
                testPlan: "Ran RetryAfterThrottleCacheTest covering expiry boundaries and cache hits.",
                breakingChanges: "None. Internal package addition guarded by feature flag.",
                rolloutAndFlag: "Guarded by ExperimentationFeatureFlag.BROKER_RETRY_AFTER_THROTTLING.",
            },
        },
    ],
    traceability: [
        {
            requirementId: "R1",
            targetIds: ["common-throttle"],
            filePaths: ["common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt"],
            testLocations: ["RetryAfterThrottleCacheTest.suppressesUntilExpiry"],
            verification: "Unit test verifies 429/503 Retry-After suppression until expiry.",
        },
        {
            requirementId: "R2",
            targetIds: ["common-throttle"],
            filePaths: ["common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt"],
            testLocations: ["RetryAfterThrottleCacheTest.servesValidCacheHits"],
            verification: "Unit test verifies cached tokens are returned during active backoff.",
        },
    ],
    assumptions: [],
    openDecisions: [],
    blockingUnknowns: [],
});

const review = (inputTask = normalizeTask({ task: task() })) => ({
    criteria: rubric.map((id) => ({ id, pass: true })),
    acceptanceCriteria: inputTask.targets.flatMap((t) =>
        t.acceptanceCriteria.map((criterion) => ({
            targetId: t.id,
            criterion,
            passed: true,
            evidence: `Verified in unit tests for ${t.id}.`,
        }))),
    findings: [],
    blockingUnknowns: [],
});

function context(results, inputTask = task(), options = {}) {
    const calls = [];
    const keys = [];
    const phases = [];
    const logs = [];
    return {
        args: { task: inputTask, options },
        calls,
        keys,
        phases,
        logs,
        phase: (title) => phases.push(title),
        log: (message) => logs.push(message),
        agent: async (prompt, agentOptions) => {
            calls.push({ prompt, options: agentOptions });
            assert.ok(results.length, `Unexpected agent call: ${agentOptions?.label}`);
            return results.shift();
        },
        step: async (key, producer) => {
            assert.ok(!keys.includes(key), `Duplicate step key: ${key}`);
            keys.push(key);
            return producer();
        },
    };
}

test("missing, stale, or incomplete task approval requires clarification without spawning agents", async () => {
    for (const badInput of [
        undefined,
        {},
        { ...task(), approval: undefined },
        { ...task(), approval: { ...task().approval, approvedRevision: "stale-rev" } },
        { ...task(), blockingUnknowns: ["Which status codes trigger backoff?"] },
        { ...task(), requirements: [task().requirements[0], task().requirements[0]] },
        { ...task(), targets: [] },
        { ...task(), targets: [{ ...task().targets[0], workItemId: 0 }] },
        { ...task(), targets: [{ ...task().targets[0], acceptanceCriteria: [] }] },
    ]) {
        const ctx = context([], badInput);
        ctx.args = { task: badInput };
        assert.ok(validateTask(ctx.args).length > 0);
        const result = await runCodeFeature(ctx);
        assert.equal(result.recommendation, "needs clarification");
        assert.equal(ctx.calls.length, 0);
        assert.equal(result.pullRequests.length, 0);
    }
});

test("single-repository shorthand task normalizes defaults for Android Auth repos", () => {
    const normalized = normalizeTask({
        task: {
            title: "Fix broker IPC timeout",
            revision: "rev-1",
            workItemId: 3809999,
            repository: "broker",
            approval: { approvedRevision: "rev-1", approvedBy: "owner", reference: "ref-1" },
            requirements: [{ id: "R1", text: "Prevent IPC hang on timeout." }],
            blockingUnknowns: [],
        },
    });
    assert.equal(normalized.targets.length, 1);
    assert.equal(normalized.targets[0].hostname, "msft.ghe.com");
    assert.equal(normalized.targets[0].slug, "security/ad-accounts-for-android");
    assert.equal(normalized.targets[0].baseBranch, "dev");
    assert.deepEqual(normalized.targets[0].acceptanceCriteria, ["Prevent IPC hang on timeout."]);
});

test("valid implementation passing first review exits loop immediately and prepares perfect PR packet", async () => {
    const ctx = context([evidence(), implementation(), review()], task(), {
        builderModel: "gpt-6.1-sol",
        reviewerModel: "gemini-4-argon",
    });
    const packet = await runCodeFeature(ctx);
    assert.equal(packet.recommendation, "GO to PR Review");
    assert.equal(packet.humanApproval, "pending");
    assert.equal(packet.fixIterations, 0);
    assert.equal(ctx.calls.length, 3);
    assert.equal(ctx.calls[0].options.agent, EVIDENCE_AGENT);
    assert.equal(ctx.calls[1].options.agent, BUILDER_AGENT);
    assert.equal(ctx.calls[1].options.model, "gpt-6.1-sol");
    assert.equal(ctx.calls[2].options.agent, REVIEWER_AGENT);
    assert.equal(ctx.calls[2].options.model, "gemini-4-argon");
    assert.equal(packet.pullRequests.length, 1);
    const pr = packet.pullRequests[0];
    assert.equal(pr.title, "Add Retry-After throttle cache in Common, Fixes AB#3802484");
    assert.match(pr.body, /Fixes AB#3802484 · Related to AB#3802483/);
    assert.match(pr.body, /## Requirements Traceability/);
    assert.match(pr.body, /RetryAfterThrottleCacheTest\.suppressesUntilExpiry/);
});

test("reviewer feedback loops back to builder up to 2 times and succeeds when 2nd fix passes review", async () => {
    const makeFailedReview = (roundNum) => ({
        ...review(),
        findings: [
            {
                id: `F-${roundNum}`,
                rubricId: "concurrencyAndThreadSafety",
                severity: "major",
                targetId: "common-throttle",
                file: "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt",
                location: "RetryAfterThrottleCache",
                evidence: `Round ${roundNum} concurrency issue found.`,
                requiredChange: `Fix concurrency issue from round ${roundNum}.`,
            },
        ],
    });
    const ctx = context([
        evidence(),
        implementation(),
        makeFailedReview(1),
        implementation(),
        makeFailedReview(2),
        implementation(),
        review(),
    ]);
    const packet = await runCodeFeature(ctx);
    assert.equal(packet.recommendation, "GO to PR Review");
    assert.equal(packet.fixIterations, 2);
    assert.equal(packet.reviews.length, 3);
    assert.equal(ctx.calls.length, 7);
    assert.equal(ctx.calls[3].options.label, "Code revision (fix 1)");
    assert.match(ctx.calls[3].prompt, /Round 1 concurrency issue found/);
    assert.equal(ctx.calls[5].options.label, "Code revision (fix 2)");
    assert.match(ctx.calls[5].prompt, /Round 2 concurrency issue found/);
    assert.equal(packet.pullRequests.length, 1);
});

test("reaching max 2 fix iterations with unresolved reviewer findings stops with NO-GO and creates no PRs", async () => {
    const failedReview = () => ({
        ...review(),
        findings: [
            {
                id: "F-unresolved",
                rubricId: "correctnessAndEdgeCases",
                severity: "blocker",
                targetId: "common-throttle",
                file: "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt",
                location: "RetryAfterThrottleCache",
                evidence: "Clock skew handling is still broken.",
                requiredChange: "Use monotonic clock source.",
            },
        ],
    });
    const ctx = context([
        evidence(),
        implementation(),
        failedReview(),
        implementation(),
        failedReview(),
        implementation(),
        failedReview(),
    ]);
    const packet = await runCodeFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.fixIterations, MAX_FIX_ITERATIONS);
    assert.equal(packet.reviews.length, 3);
    assert.equal(packet.pullRequests.length, 0);
    assert.equal(ctx.calls.length, 7);
});

test("structural validation catches unsafe paths, missing tests, and broken traceability before reviewer runs", async () => {
    assert.equal(isSafeRelativeFilePath("../secret.txt"), false);
    assert.equal(isSafeRelativeFilePath("C:\\temp\\a.kt"), false);
    assert.equal(isSafeRelativeFilePath(".git/config"), false);
    assert.equal(isSafeRelativeFilePath("common4j/src/main/Example.kt"), true);

    const normalized = normalizeTask({ task: task() });
    for (const mutate of [
        (impl) => { impl.taskRevision = "wrong"; },
        (impl) => { impl.targets[0].files[0].path = "../escape.kt"; },
        (impl) => { impl.targets[0].testsAddedOrUpdated = []; },
        (impl) => { impl.targets[0].pr.testPlan = " "; },
        (impl) => { impl.traceability = [impl.traceability[0]]; },
        (impl) => { impl.traceability[0].filePaths = ["nonexistent.kt"]; },
    ]) {
        const candidate = implementation();
        mutate(candidate);
        assert.ok(validateImplementation(candidate, normalized).length > 0);
    }

    const brokenImpl = implementation();
    brokenImpl.targets[0].testsAddedOrUpdated = [];
    const ctx = context([evidence(), brokenImpl, implementation(), review()]);
    const packet = await runCodeFeature(ctx);
    assert.equal(packet.recommendation, "GO to PR Review");
    assert.equal(packet.fixIterations, 1);
    assert.ok(packet.reviews[0].validationErrors.length > 0);
});

test("live createPullRequests option writes files, commits, pushes, and invokes gh pr create per repo", async () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-test-"));
    try {
        const commonDir = path.join(tempRoot, "common");
        const brokerDir = path.join(tempRoot, "broker");
        for (const dir of [commonDir, brokerDir]) {
            writeFileSync(path.join(tempRoot, ".gitkeep"), "");
            mkdirSync(dir, { recursive: true });
        }
        const multiTask = {
            ...task(),
            targets: [
                task().targets[0],
                {
                    id: "broker-throttle",
                    repository: "broker",
                    baseBranch: "dev",
                    workItemId: 3802485,
                    title: "Wire Retry-After throttle cache in Broker",
                    description: "Use Common throttle cache in Broker controller.",
                    acceptanceCriteria: ["Suppresses duplicate broker token calls during backoff"],
                    verificationCommands: ["./gradlew :broker4j:test"],
                },
            ],
        };
        const multiImpl = {
            ...implementation(),
            targets: [
                implementation().targets[0],
                {
                    targetId: "broker-throttle",
                    repository: "broker",
                    summary: "Wired RetryAfterThrottleCache in BrokerMsalController.",
                    files: [
                        {
                            path: "broker4j/src/main/kotlin/com/microsoft/identity/client/kerberos/BrokerThrottleWiring.kt",
                            action: "create",
                            content: "// Copyright (c) Microsoft Corporation. All rights reserved.\npackage com.microsoft.identity.client.kerberos\nclass BrokerThrottleWiring\n",
                            summary: "Broker throttle integration.",
                        },
                    ],
                    testsAddedOrUpdated: ["BrokerThrottleWiringTest.suppressesDuplicateCalls"],
                    verificationCommandsRun: ["./gradlew :broker4j:test"],
                    pr: {
                        title: "Wire Retry-After throttle cache in Broker, Fixes AB#3802485",
                        summary: "Integrates Common Retry-After cache into Broker request path.",
                        testPlan: "Ran BrokerThrottleWiringTest.",
                        breakingChanges: "None.",
                        rolloutAndFlag: "Guarded by ExperimentationFeatureFlag.BROKER_RETRY_AFTER_THROTTLING.",
                    },
                },
            ],
            traceability: [
                implementation().traceability[0],
                {
                    requirementId: "R2",
                    targetIds: ["common-throttle", "broker-throttle"],
                    filePaths: [
                        "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt",
                        "broker4j/src/main/kotlin/com/microsoft/identity/client/kerberos/BrokerThrottleWiring.kt",
                    ],
                    testLocations: ["BrokerThrottleWiringTest.suppressesDuplicateCalls"],
                    verification: "Verified across Common and Broker.",
                },
            ],
        };

        const execCalls = [];
        const mockExec = (cmd, args, opts) => {
            execCalls.push({ cmd, args, cwd: opts?.cwd });
            if (cmd === "gh" && args[0] === "auth" && args[1] === "status") {
                const host = args[3];
                return host === "msft.ghe.com"
                    ? "Logged in to msft.ghe.com account shjameel (keyring)"
                    : "Logged in to github.com account shahzaibj (keyring)";
            }
            if (cmd === "gh" && args[0] === "auth" && args[1] === "token") return "mock-token";
            if (cmd === "git" && args[0] === "rev-parse") return "deadbeef1234";
            if (cmd === "gh" && args[0] === "pr" && args[1] === "create") {
                const repoArg = args[args.indexOf("--repo") + 1];
                return `https://${repoArg.startsWith("msft.ghe.com/") ? repoArg : `github.com/${repoArg}`}/pull/999`;
            }
            return "";
        };

        const normalizedMulti = normalizeTask({ task: multiTask });
        const ctx = context(
            [evidence(), multiImpl, review(normalizedMulti)],
            multiTask,
            { workspaceRoot: tempRoot, createPullRequests: true },
        );
        const packet = await runCodeFeature(ctx, { exec: mockExec });
        assert.equal(packet.recommendation, "ci-not-observed");
        assert.equal(packet.pullRequests.length, 2);
        assert.equal(packet.pullRequests[0].status, "created");
        assert.equal(packet.pullRequests[0].url, "https://github.com/AzureAD/microsoft-authentication-library-common-for-android/pull/999");
        assert.equal(packet.pullRequests[1].status, "created");
        assert.equal(packet.pullRequests[1].url, "https://msft.ghe.com/security/ad-accounts-for-android/pull/999");
        assert.ok(existsSync(path.join(commonDir, "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt")));
        assert.ok(execCalls.some((c) => c.cmd === "./gradlew" && c.args.includes(":broker4j:test")));
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("failed workspace verification restores original files on disk before exiting with NO-GO", async () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-restore-"));
    try {
        const commonDir = path.join(tempRoot, "common");
        mkdirSync(commonDir, { recursive: true });
        const existingFile = path.join(commonDir, "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt");
        mkdirSync(path.dirname(existingFile), { recursive: true });
        writeFileSync(existingFile, "original-content", "utf8");

        const taskWithCheck = task();
        taskWithCheck.targets[0].verificationCommands = ["./gradlew :common4j:test"];

        const ctx = context(
            [evidence(), implementation()],
            taskWithCheck,
            { workspaceRoot: tempRoot, applyToWorkspace: true, maxFixIterations: 1 },
        );
        // After round 0 fails verification, fix-1 returns null so it stops with NO-GO and restores files.
        ctx.agent = async (_prompt, opts) => {
            if (opts.label === "Repository evidence") return evidence();
            if (opts.label === "Code builder (round 1)") return implementation();
            return null;
        };
        const packet = await runCodeFeature(ctx, {
            exec: (cmd) => {
                if (/gradlew/i.test(cmd)) throw new Error("Gradle test failed");
                return "";
            },
        });
        assert.equal(packet.recommendation, "NO-GO");
        assert.equal(readFileSync(existingFile, "utf8"), "original-content");
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("mutating input during review fails closed and journal replay avoids duplicate agent or PR calls", async () => {
    const mutatedCtx = context([evidence(), implementation(), review()]);
    const origAgent = mutatedCtx.agent;
    mutatedCtx.agent = async (prompt, opts) => {
        if (opts.agent === REVIEWER_AGENT) mutatedCtx.args.task.requirements[0].text = "Tampered requirement";
        return origAgent(prompt, opts);
    };
    const mutatedPacket = await runCodeFeature(mutatedCtx);
    assert.equal(mutatedPacket.recommendation, "NO-GO");
    assert.equal(mutatedPacket.pullRequests.length, 0);

    const journal = new Map();
    const step = async (key, producer) => {
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    const firstRun = context([evidence(), implementation(), review()]);
    firstRun.step = step;
    assert.equal((await runCodeFeature(firstRun)).recommendation, "GO to PR Review");

    const resumedRun = context([]);
    resumedRun.step = step;
    const resumedPacket = await runCodeFeature(resumedRun);
    assert.equal(resumedPacket.recommendation, "GO to PR Review");
    assert.equal(resumedRun.calls.length, 0);
});

test("customAgents enforce read-only reviewer permissions and workflow metadata has explicit bounds", () => {
    const reviewer = customAgents.find((a) => a.name === REVIEWER_AGENT);
    const builder = customAgents.find((a) => a.name === BUILDER_AGENT);
    const evidenceWorker = customAgents.find((a) => a.name === EVIDENCE_AGENT);
    assert.deepEqual(reviewer.tools, ["view", "glob", "grep", "skill"]);
    assert.deepEqual(evidenceWorker.tools, ["view", "glob", "grep", "skill"]);
    assert.ok(builder.tools.includes("edit") && builder.tools.includes("skill"));
    assert.ok(resolveRoleSkillGuides("evidence").includes(".github/skills/codebase-researcher/SKILL.md"));
    assert.ok(resolveRoleSkillGuides("builder", [{ repository: "common" }]).includes(".github/skills/code-author/SKILL.md"));
    assert.ok(resolveRoleSkillGuides("builder", [{ repository: "common" }]).includes("common/.github/copilot-instructions.md"));
    assert.ok(resolveRoleSkillGuides("reviewer", [{ repository: "common" }]).includes(".github/skills/code-author/SKILL.md"));
    assert.ok(resolveRoleSkillGuides("reviewer", [{ repository: "common" }]).includes("common/.github/skills/code-review/SKILL.md"));
    assert.ok(resolveRoleSkillGuides("reviewer", [{ repository: "broker" }]).includes("broker/.github/skills/code-review/SKILL.md"));
    assert.equal(workflowDefinition.meta.limits.maxAiCredits, undefined);
    assert.equal(workflowDefinition.meta.limits.timeoutSeconds, undefined);
    assert.equal(workflowDefinition.meta.name, "code-feature");
    assert.equal(formatPrTitle("Add cache", 12345), "Add cache, Fixes AB#12345");
    assert.equal(formatPrTitle("Add cache, Fixes AB#12345", 12345), "Add cache, Fixes AB#12345");
    assert.match(
        formatPrBody(normalizeTask({ task: task() }).targets[0], implementation().targets[0], implementation().traceability, 1, 3802483),
        /Independent AI code review passed in 1 round\(s\)/,
    );
});

test("supports designDocPath, PBI IDs, and free-form prompt inputs end-to-end", async () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-inputs-"));
    try {
        const docPath = path.join(tempRoot, "broker-throttling-design.md");
        writeFileSync(
            docPath,
            [
                "# Add server-directed Retry-After throttling",
                "",
                "Tracking: AB#3802484",
                "Target repository: common",
                "",
                "## Requirements",
                "- Honor valid Retry-After headers on 429/503 responses.",
                "- Serve valid cached tokens during active backoff.",
            ].join("\n"),
            "utf8",
        );

        const extractedSpec = {
            title: "Add server-directed Retry-After throttling",
            designSummary: "Honor eSTS Retry-After headers in Common.",
            requirements: task().requirements,
            targets: task().targets,
        };
        const evidenceWithSpec = () => ({
            ...evidence(),
            extractedSpec,
            clarificationQuestions: [],
        });

        // 1. Design doc path input
        const designCtx = context([evidenceWithSpec(), implementation(), review()]);
        designCtx.args = {
            designDocPath: "broker-throttling-design.md",
            options: { workspaceRoot: tempRoot },
        };
        const designPacketResult = await runCodeFeature(designCtx);
        assert.equal(designPacketResult.recommendation, "GO to PR Review");
        assert.equal(designPacketResult.task.inputMode, "design_doc");
        assert.equal(designPacketResult.pullRequests.length, 1);

        // 2. PBI ID input (fetched via fetchWorkItem)
        const pbiCtx = context([evidenceWithSpec(), implementation(), review()]);
        pbiCtx.args = { pbiId: 3802484 };
        const pbiPacketResult = await runCodeFeature(pbiCtx, {
            fetchWorkItem: (id) => ({
                id,
                fields: {
                    "System.Title": "Add Retry-After throttle cache in Common",
                    "System.Description": "<p>Target repo: common</p><ul><li>Honor valid Retry-After headers on 429/503 responses.</li><li>Serve valid cached tokens during active backoff.</li></ul>",
                    "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Suppresses matching requests until Retry-After expires</li><li>Unit tests verify expiry and cache hit behavior</li></ul>",
                    "System.Parent": 3802483,
                },
            }),
        });
        assert.equal(pbiPacketResult.recommendation, "GO to PR Review");
        assert.equal(pbiPacketResult.task.inputMode, "pbi");
        assert.equal(pbiPacketResult.pullRequests[0].workItemId, 3802484);

        // 3. Free-form prompt input
        const promptCtx = context([evidenceWithSpec(), implementation(), review()]);
        promptCtx.args = {
            prompt: "Add server-directed Retry-After throttling to Common for AB#3802484",
        };
        const promptPacketResult = await runCodeFeature(promptCtx);
        assert.equal(promptPacketResult.recommendation, "GO to PR Review");
        assert.equal(promptPacketResult.task.inputMode, "prompt");
        assert.equal(promptPacketResult.pullRequests.length, 1);
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("clarification questions are asked only when requirements are unclear and resume coding when answered", async () => {
    const ambiguousEvidence = () => ({
        ...evidence(),
        extractedSpec: {
            title: "Add throttling",
            designSummary: "Add throttling.",
            requirements: task().requirements,
            targets: task().targets,
        },
        clarificationQuestions: ["Should throttling apply to interactive token requests or only silent requests?"],
    });

    // Without interactive askUser, returns needs clarification with the exact question
    const nonInteractiveCtx = context([ambiguousEvidence()]);
    nonInteractiveCtx.args = { prompt: "Add throttling to Common (AB#3802484)" };
    const pausedPacket = await runCodeFeature(nonInteractiveCtx);
    assert.equal(pausedPacket.recommendation, "needs clarification");
    assert.deepEqual(pausedPacket.questions, [
        "Should throttling apply to interactive token requests or only silent requests?",
    ]);
    assert.equal(nonInteractiveCtx.calls.length, 1);

    // With interactive askUser, asks the question once, records the answer, and completes coding
    // Also verifies that numeric HTTP status codes like "429" in non-work-item answers do NOT overwrite workItemId.
    const asked = [];
    const interactiveCtx = context([ambiguousEvidence(), implementation(), review()]);
    interactiveCtx.args = { prompt: "Add throttling to Common (AB#3802484)" };
    const completedPacket = await runCodeFeature(interactiveCtx, {
        askUser: async (question) => {
            asked.push(question);
            return "Only silent token requests on HTTP 429 / 5xx / Retry-After.";
        },
    });
    assert.equal(completedPacket.recommendation, "GO to PR Review");
    assert.equal(asked.length, 1);
    assert.equal(completedPacket.task.workItemId, 3802484);
    assert.equal(completedPacket.pullRequests[0].workItemId, 3802484);
    assert.deepEqual(completedPacket.task.sourceInputs.clarifications, [
        {
            question: "Should throttling apply to interactive token requests or only silent requests?",
            answer: "Only silent token requests on HTTP 429 / 5xx / Retry-After.",
        },
    ]);
});

test("ensureIsolatedTargetWorktree replaces a shared junction and validateImplementation accepts on-disk edits with empty content", async () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-wt-"));
    try {
        const commonDir = path.join(tempRoot, "common");
        const onDiskFile = path.join(commonDir, "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt");
        mkdirSync(path.dirname(onDiskFile), { recursive: true });
        writeFileSync(onDiskFile, "// written directly by builder tool\nclass RetryAfterThrottleCache\n", "utf8");

        const implWithEmptyContent = implementation();
        implWithEmptyContent.targets[0].files[0].content = "";
        const normalized = normalizeTask({ task: task() });
        assert.deepEqual(validateImplementation(implWithEmptyContent, normalized, { workspaceRoot: tempRoot }), []);

        const wtCalls = [];
        const fakeExec = (cmd, args) => {
            wtCalls.push({ cmd, args });
            return "";
        };
        assert.equal(
            ensureIsolatedTargetWorktree(tempRoot, { localPath: "common", baseBranch: "dev" }, fakeExec),
            commonDir,
        );
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("prompt mode with createPullRequests=false handles null workItemId, empty extractedSpec.targets, and read-only role pseudo-conflicts", async () => {
    const promptEvidence = () => ({
        ...evidence(),
        extractedSpec: {
            title: "Add silent request throttling to Broker",
            designSummary: "Throttle silent requests on HTTP 429/5xx/Retry-After.",
            requirements: task().requirements,
            targets: [],
        },
        conflicts: [
            "Prompt mentions running code-builder and code-reviewer which conflicts with read-only evidence role.",
            "Missing integer workItemId for AB# linking.",
        ],
        clarificationQuestions: [
            "What fallback duration and max duration should apply when Retry-After is absent?",
            "What throttle key scope should be used?",
        ],
    });

    const impl = implementation();
    impl.targets[0].targetId = "target-1";
    impl.traceability = impl.traceability.map((row) => ({ ...row, targetIds: ["target-1"] }));
    const rev = review();
    rev.acceptanceCriteria = task().requirements.map((req) => ({
        targetId: "target-1",
        criterion: req.text,
        passed: true,
        evidence: "RetryAfterThrottleCacheTest:L12-L40",
    }));

    const ctx = context([promptEvidence(), impl, rev]);
    ctx.args = {
        prompt: "add throttling to common",
        options: { createPullRequests: false },
    };
    const answers = [
        "30s default fallback, 3600s max Retry-After cap",
        "calling package UID / sign hash",
    ];
    let idx = 0;
    const result = await runCodeFeature(ctx, {
        askUser: async () => answers[idx++],
    });
    assert.equal(result.recommendation, "GO to PR Review");
    assert.equal(result.task.targets.length, 1);
    assert.equal(result.task.targets[0].workItemId, null);
    assert.equal(result.fixIterations, 0);
});

test("iterative intake loop re-invokes evidence after clarifications and supports brief refine->start approval", async () => {
    const round1Evidence = () => ({
        ...evidence(),
        extractedSpec: {
            title: "Add throttling",
            designSummary: "Initial draft.",
            requirements: [{ id: "R1", text: "Throttle silent requests." }],
            targets: [],
        },
        clarificationQuestions: ["What fallback duration should apply when Retry-After is absent?"],
    });
    const round2Evidence = () => ({
        ...evidence(),
        extractedSpec: {
            title: "Add 30s fallback throttling",
            designSummary: "Refined with 30s fallback.",
            requirements: task().requirements,
            targets: task().targets,
        },
        clarificationQuestions: [],
    });
    const round3Evidence = () => ({
        ...evidence(),
        extractedSpec: {
            title: "Add 30s fallback + UID-scoped throttling",
            designSummary: "Refined with UID scope.",
            requirements: task().requirements,
            targets: task().targets,
        },
        clarificationQuestions: [],
    });

    const ctx = context([round1Evidence(), round2Evidence(), round3Evidence(), implementation(), review()]);
    ctx.args = {
        prompt: "add throttling to common",
        options: {
            createPullRequests: false,
            iterativeIntake: true,
            requireBriefApproval: true,
        },
    };
    let briefCalls = 0;
    const result = await runCodeFeature(ctx, {
        askUser: async () => "Use 30s fallback and 3600s max cap.",
        askBriefApproval: async () => {
            briefCalls++;
            return briefCalls === 1
                ? { decision: "refine", feedback: "Also key by calling package UID." }
                : { decision: "start" };
        },
    });
    assert.equal(result.recommendation, "GO to PR Review");
    assert.equal(briefCalls, 2);
    assert.equal(result.task.title, "Add 30s fallback + UID-scoped throttling");
});

test("pre-review local verification gate catches Kotlin/Lombok compile errors before reviewer and defers missing local VSTS credentials", async () => {
    assert.equal(
        extractRunnableVerificationCommand("From broker, if the policy is placed in broker4j: .\\gradlew.bat :broker4j:test"),
        ".\\gradlew.bat :broker4j:test",
    );
    assert.deepEqual(
        parseAllowedGradleVerificationCommand("From broker: .\\gradlew.bat :broker4j:test --tests \"SilentRequestThrottleTest\" --max-workers=2 --no-daemon"),
        {
            command: ".\\gradlew.bat :broker4j:test --tests SilentRequestThrottleTest --max-workers=2 --no-daemon",
            executable: ".\\gradlew.bat",
            args: [":broker4j:test", "--tests", "SilentRequestThrottleTest", "--max-workers=2", "--no-daemon"],
        },
    );
    assert.deepEqual(
        parseAllowedGradleVerificationCommand("cd authenticator/PhoneFactor && .\\gradlew.bat :app:compileProductionDebugKotlin :app:compileProductionDebugAndroidTestKotlin"),
        {
            command: ".\\gradlew.bat :app:compileProductionDebugKotlin :app:compileProductionDebugAndroidTestKotlin",
            executable: ".\\gradlew.bat",
            args: [":app:compileProductionDebugKotlin", ":app:compileProductionDebugAndroidTestKotlin"],
        },
    );
    assert.equal(parseAllowedGradleVerificationCommand(".\\gradlew.bat :common4j:test; Invoke-WebRequest https://evil.example"), null);
    assert.equal(parseAllowedGradleVerificationCommand(".\\gradlew.bat --init-script evil.gradle :common4j:test"), null);
    assert.equal(parseAllowedGradleVerificationCommand(".\\gradlew.bat publishToMavenLocal"), null);
    assert.equal(parseAllowedGradleVerificationCommand("powershell -Command Get-ChildItem"), null);
    const connectedParsed = parseAllowedGradleVerificationCommand(
        ".\\gradlew.bat :app:connectedProductionDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.microsoft.authenticator.GuidedRestoreNavHostTest",
    );
    assert.equal(connectedParsed?.deferredToCi, true);

    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-gate-"));
    try {
        const commonDir = path.join(tempRoot, "common");
        mkdirSync(commonDir, { recursive: true });
        writeFileSync(path.join(commonDir, "gradlew.bat"), "@echo off\r\necho GRADLE_OK %*\r\n", "utf8");

        // 0a. Real Windows .bat execution (including ./gradlew normalization and directory with spaces) + connectedAndroidTest deferral
        if (process.platform === "win32") {
            const spaceDir = path.join(tempRoot, "dir with spaces", "PhoneFactor");
            mkdirSync(spaceDir, { recursive: true });
            writeFileSync(path.join(spaceDir, "gradlew.bat"), "@echo off\r\necho GRADLE_SPACE_OK %*\r\n", "utf8");
            const realImpl = implementation();
            const realGate = runLocalVerificationGate({
                repoRoot: path.dirname(spaceDir),
                target: {
                    id: "authenticator-guided-restore",
                    verificationCommands: [
                        "./gradlew :app:compileProductionDebugKotlin :app:compileProductionDebugAndroidTestKotlin",
                        ".\\gradlew.bat :app:testProductionDebugUnitTest --tests com.microsoft.authenticator.EntraRestoreEntryPointCoordinatorTest",
                        ".\\gradlew.bat :app:connectedProductionDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.microsoft.authenticator.GuidedRestoreNavHostTest",
                    ],
                },
                targetImpl: realImpl.targets[0],
                implementation: realImpl,
            });
            assert.deepEqual(realGate.errors, []);
            assert.equal(realGate.ranCommands.length, 2);
            assert.equal(realGate.deferredNotes.length, 1);
            assert.throws(
                () => runWindowsBatchFileSafely(path.join(spaceDir, "gradlew.bat"), [":app:test & calc.exe"], { cwd: spaceDir }),
                /unsafe argument/i,
            );
        }

        // 0. Rejects non-allowlisted commands without executing anything or fetching tokens
        let unsafeExecCount = 0;
        const rejectedGate = runLocalVerificationGate({
            repoRoot: commonDir,
            target: {
                id: "common-throttling",
                verificationCommands: [".\\gradlew.bat :common4j:test; whoami", "Get-Process"],
            },
            targetImpl: null,
            implementation: null,
            exec: () => {
                unsafeExecCount++;
                return "";
            },
        });
        assert.equal(unsafeExecCount, 0);
        assert.equal(rejectedGate.errors.length, 2);

        // 1. Missing local VSTS credentials in settings.gradle -> deferred to CI, not a failure, and no credentials exposed
        const envImpl = implementation();
        const prevToken = process.env.ENV_VSTS_MVN_CRED_ACCESSTOKEN;
        process.env.ENV_VSTS_MVN_CRED_ACCESSTOKEN = "secret-token-should-be-scrubbed";
        let capturedEnv = null;
        try {
            const envGate = runLocalVerificationGate({
                repoRoot: commonDir,
                target: {
                    id: "common-throttling",
                    verificationCommands: ["From common: .\\gradlew.bat :common4j:test"],
                },
                targetImpl: envImpl.targets[0],
                implementation: envImpl,
                exec: (cmd, _args, opts) => {
                    assert.notEqual(cmd, "az");
                    assert.notEqual(cmd, "powershell");
                    capturedEnv = opts?.env;
                    throw new Error("Could not get unknown property 'project' for Credentials in settings.gradle:13");
                },
            });
            assert.deepEqual(envGate.errors, []);
            assert.equal(envGate.deferredNotes.length, 1);
            assert.equal(capturedEnv?.ENV_VSTS_MVN_CRED_ACCESSTOKEN, undefined);
        } finally {
            if (prevToken === undefined) {
                delete process.env.ENV_VSTS_MVN_CRED_ACCESSTOKEN;
            } else {
                process.env.ENV_VSTS_MVN_CRED_ACCESSTOKEN = prevToken;
            }
        }

        // 2. Real Kotlin/Lombok compile failure -> triggers builder fix-1 before independent reviewer runs
        let gradleAttempt = 0;
        const brokenImpl = implementation();
        const fixedImpl = implementation();
        const gateTask = task();
        gateTask.targets[0].verificationCommands = ["From common: .\\gradlew.bat :common4j:test"];
        const ctx = context([evidence(), brokenImpl, fixedImpl, review()]);
        ctx.args = {
            task: gateTask,
            options: {
                workspaceRoot: tempRoot,
                runLocalVerification: true,
                createPullRequests: false,
            },
        };
        const result = await runCodeFeature(ctx, {
            exec: (cmd) => {
                if (/gradlew/i.test(cmd)) {
                    gradleAttempt++;
                    if (gradleAttempt === 1) {
                        throw new Error("> Task :broker4j:compileKotlin FAILED\nUnresolved reference: isShouldResolveInterrupt");
                    }
                    return "BUILD SUCCESSFUL";
                }
                return "";
            },
        });
        assert.equal(result.recommendation, "GO to PR Review");
        assert.equal(result.fixIterations, 1);
        assert.equal(gradleAttempt, 2);
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("pauseOnBlocker invokes ctx.pause on unresolved clarification blockers and snapshotWorktree detects read-only worktree drift", async () => {
    const pausedKeys = [];
    const ctx = context([
        {
            ...evidence(),
            blockingUnknowns: ["Need production tenant allowlist policy."],
        },
    ]);
    ctx.pause = async (key) => {
        pausedKeys.push(key);
    };
    ctx.args = {
        task: task(),
        options: { pauseOnBlocker: true },
    };
    const pausedResult = await runCodeFeature(ctx);
    assert.equal(pausedResult.recommendation, "needs clarification");
    assert.equal(pausedKeys.length, 1);

    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-snap-"));
    try {
        const repoDir = path.join(tempRoot, "common");
        mkdirSync(path.join(repoDir, ".git"), { recursive: true });
        let head = "abc1234";
        const fakeGitExec = (_cmd, args) => {
            if (args.includes("rev-parse")) return head;
            return "";
        };
        const snap1 = snapshotWorktree(repoDir, fakeGitExec);
        head = "def5678";
        const snap2 = snapshotWorktree(repoDir, fakeGitExec);
        assert.ok(snap1);
        assert.ok(snap2);
        assert.notEqual(snap1, snap2);
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("thin { verdict, summary, evidence } schemas and on-disk git file discovery work end-to-end", async () => {
    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-slim-"));
    try {
        const brokerDir = path.join(tempRoot, "broker");
        const prodFile = path.join(brokerDir, "broker4j/src/main/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottle.kt");
        const testFile = path.join(brokerDir, "broker4j/src/test/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottleTest.kt");
        mkdirSync(path.join(brokerDir, ".git"), { recursive: true });
        mkdirSync(path.dirname(prodFile), { recursive: true });
        mkdirSync(path.dirname(testFile), { recursive: true });
        writeFileSync(prodFile, "// Copyright (c) Microsoft Corporation.\nclass SilentRequestThrottle\n", "utf8");
        writeFileSync(testFile, "// Copyright (c) Microsoft Corporation.\nclass SilentRequestThrottleTest\n", "utf8");

        const slimIntake = {
            verdict: "ready",
            summary: "Add caller-bound silent request throttling in Broker.",
            repository: "broker",
            baseBranch: "dev",
            workItemId: null,
            requirements: [
                "Throttle AcquireTokenSilent on HTTP 429/5xx/Retry-After.",
                "Fail immediately with ServiceException preserving eSTS error.",
            ],
            verificationCommands: [],
            questions: [],
            evidence: ["broker4j/src/main/com/microsoft/identity/broker4j/broker/ipc/AuthSdkOperation.java:L346-L390"],
        };
        const slimBuild = {
            verdict: "pass",
            summary: "Implemented SilentRequestThrottle and unit tests in broker4j.",
            evidence: [
                "broker4j/src/main/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottle.kt",
                "broker4j/src/test/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottleTest.kt",
            ],
            prTitle: "Add silent request server-directed throttling in Broker",
            prSummary: "Adds caller-isolated Retry-After throttling for Broker AcquireTokenSilent.",
            testPlan: ".\\gradlew.bat :broker4j:test",
            rolloutAndFlag: "BrokerFlight.ENABLE_SILENT_REQUEST_THROTTLING (default false)",
        };
        const slimReviewRound1 = {
            verdict: "repair",
            summary: "Preserve UiRequiredException subtype during throttle replay.",
            evidence: ["SilentRequestThrottle.kt:L42"],
            findings: ["SilentRequestThrottle.kt: Preserve UiRequiredException and IntuneAppProtectionPolicyRequiredException subtypes."],
        };
        const slimReviewRound2 = {
            verdict: "pass",
            summary: "Subtype preservation and regression tests verified.",
            evidence: ["SilentRequestThrottleTest.kt:L10-L85"],
            findings: [],
        };

        const ctx = context([slimIntake, slimBuild, slimReviewRound1, slimBuild, slimReviewRound2]);
        ctx.session = {};
        ctx.args = {
            prompt: "add throttling to broker",
            options: {
                workspaceRoot: tempRoot,
                createPullRequests: false,
                slimSchemas: true,
            },
        };
        const fakeGitExec = (_cmd, args) => {
            if (args.includes("status") && args.includes("--porcelain")) {
                return [
                    " M broker4j/src/main/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottle.kt",
                    "?? broker4j/src/test/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottleTest.kt",
                ].join("\n").trim();
            }
            if (args.includes("rev-parse")) return "e428039b7";
            return "";
        };
        const discovered = discoverChangedFilesFromGit(brokerDir, fakeGitExec);
        assert.equal(discovered.files.length, 2);
        assert.equal(
            discovered.files[0].path,
            "broker4j/src/main/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottle.kt",
        );
        assert.equal(discovered.testsAddedOrUpdated.length, 1);

        const packet = await runCodeFeature(ctx, { exec: fakeGitExec });
        assert.equal(packet.recommendation, "GO to PR Review");
        assert.equal(packet.fixIterations, 1);
        assert.equal(ctx.calls[0].options.schema, slimIntakeSchema);
        assert.equal(ctx.calls[1].options.schema, slimResultSchema);
        assert.equal(ctx.calls[2].options.schema, slimReviewSchema);
    } finally {
        rmSync(tempRoot, { recursive: true, force: true });
    }
});

test("pr-observer workflow inspects PR checks/threads, applies verified tactical repair, pushes, and reaches ready-for-human-merge", async () => {
    assert.equal(observerWorkflowDefinition.meta.name, "pr-observer");
    assert.equal(observerCustomAgent.name, OBSERVER_AGENT);
    const parsedGhe = parsePullRequestUrl("https://msft.ghe.com/security/ad-accounts-for-android/pull/362");
    assert.equal(parsedGhe.hostname, "msft.ghe.com");
    assert.equal(parsedGhe.repository, "broker");
    assert.equal(parsedGhe.prNumber, 362);
    assert.equal(parsePullRequestUrl("https://evil.example.com/AzureAD/android-complete/pull/1"), null);
    assert.equal(parsePullRequestUrl("https://github.com/octocat/Hello-World/pull/1"), null);
    assert.equal(parsePullRequestUrl("https://msazure.visualstudio.com/One/_git/UnrelatedRepo/pullrequest/99"), null);
    assert.equal(
        verifyCheckoutRemoteMatchesPr("C:\\repo\\broker", parsedGhe, () => "https://github.com/octocat/Hello-World.git").ok,
        false,
    );
    assert.equal(
        verifyCheckoutRemoteMatchesPr("C:\\repo\\broker", parsedGhe, () => "https://msft.ghe.com/security/ad-accounts-for-android.git").ok,
        true,
    );

    const observeRepair = {
        verdict: "repair",
        repairKind: "review-comment",
        summary: "CI :broker4j:compileKotlin failed on SilentRequestIdentity.kt:51.",
        evidence: ["https://msft.ghe.com/security/ad-accounts-for-android/pull/362/checks"],
        actionableFindings: ["SilentRequestIdentity.kt:51: Declare explicit isShouldResolveInterrupt() Java getter in BrokerSilentTokenCommandParametersForResourceAccount."],
    };
    const builderFix = {
        verdict: "pass",
        summary: "Declared explicit isShouldResolveInterrupt() getter and added unit test.",
        evidence: ["SilentRequestIdentity.kt", "SilentRequestIdentityTest.kt"],
        prTitle: "Fix same-module Kotlin/Lombok getter resolution",
        prSummary: "Declares explicit getter for Kotlin compilation order.",
        testPlan: ".\\gradlew.bat :broker4j:test",
        rolloutAndFlag: "BrokerFlight.ENABLE_SILENT_REQUEST_THROTTLING",
    };
    const reviewerPass = {
        verdict: "pass",
        summary: "Verified explicit getter and regression test.",
        evidence: ["SilentRequestIdentityTest.kt"],
        findings: [],
    };
    const observeReady = {
        verdict: "ready",
        repairKind: "none",
        summary: "All CI checks green and review threads resolved.",
        evidence: ["CI checks passed", "0 unresolved threads"],
        actionableFindings: [],
    };

    const inlineThreads = [
        {
            threadId: "PRRT_addressed_1",
            id: 101,
            path: "broker4j/src/main/SilentRequestIdentity.kt",
            line: 51,
            author: "copilot-pull-request-reviewer",
            body: "Declare explicit `isShouldResolveInterrupt()` getter.",
        },
        {
            threadId: "PRRT_unrelated_same_file",
            id: 102,
            path: "broker4j/src/main/SilentRequestIdentity.kt",
            line: 140,
            author: "human-reviewer",
            body: "Unrelated question about `correlationId` formatting.",
        },
        {
            threadId: "PRRT_unrelated_other_file",
            id: 103,
            path: "broker4j/src/main/OtherClass.kt",
            line: 20,
            author: "human-reviewer",
            body: "Please keep this thread open.",
        },
    ];
    const matchedThreads = selectAddressedReviewThreads({
        threads: inlineThreads,
        observation: observeRepair,
        repairResult: builderFix,
        changedFiles: ["broker4j/src/main/SilentRequestIdentity.kt"],
    });
    assert.deepEqual(matchedThreads.map((t) => t.threadId), ["PRRT_addressed_1"]);

    const gitCommands = [];
    const ctx = context([observeRepair, builderFix, reviewerPass, observeReady]);
    ctx.args = {
        prUrl: "https://msft.ghe.com/security/ad-accounts-for-android/pull/362",
    };
    const result = await runPrObserver(ctx, {
        fetchPrSnapshot: () => ({
            number: 362,
            state: "OPEN",
            mergeStateStatus: "CLEAN",
            headRefName: "shjameel/broker-silent-server-throttling",
            headRefOid: "current-head",
            baseRefName: "dev",
            statusCheckRollup: [{ name: "CI", state: "SUCCESS" }],
            inlineReviewComments: inlineThreads,
        }),
        exec: (cmd, args) => {
            gitCommands.push({ cmd, args });
            if (cmd === "gh" && args[0] === "auth" && args[1] === "token") return "ghe-token";
            if (cmd === "git" && args[0] === "remote") return "https://msft.ghe.com/security/ad-accounts-for-android.git";
            if (cmd === "git" && args.includes("--porcelain")) return " M broker4j/src/main/SilentRequestIdentity.kt";
            return "";
        },
    });
    assert.equal(result.status, "ready-for-human-merge");
    assert.equal(result.iterations, 2);
    assert.equal(result.humanMergeOnly, true);
    assert.ok(gitCommands.some((c) => c.cmd === "git" && c.args.includes("push")));
    const resolvedThreadArgs = gitCommands
        .filter((c) => c.cmd === "gh" && c.args.includes("graphql"))
        .flatMap((c) => c.args);
    assert.ok(resolvedThreadArgs.includes("threadId=PRRT_addressed_1"));
    assert.ok(!resolvedThreadArgs.includes("threadId=PRRT_unrelated_same_file"));
    assert.ok(!resolvedThreadArgs.includes("threadId=PRRT_unrelated_other_file"));
});

test("question deduplication, pre-coding spec challenge, resume worktree inspection, PR status blocks, and observer anti-loop", async () => {
    // 1. Question deduplication + pre-coding spec challenge + PR status/delivery-decision blocks
    const round1Ev = {
        ...evidence(),
        extractedSpec: {
            title: "Add Broker throttling",
            designSummary: "Initial draft.",
            requirements: task().requirements,
            targets: task().targets,
        },
        clarificationQuestions: ["What fallback duration should apply when Retry-After is absent?"],
    };
    const round2Ev = {
        ...evidence(),
        extractedSpec: {
            title: "Add Broker throttling",
            designSummary: "Updated draft.",
            requirements: task().requirements,
            targets: task().targets,
        },
        // Repeats the exact same question on round 2 — should be deduplicated automatically without prompting again!
        clarificationQuestions: ["What fallback duration should apply when Retry-After is absent?"],
    };
    const challengePass = {
        verdict: "pass",
        summary: "Scope is sound; preserve UiRequiredException subtypes during replay.",
        evidence: ["ExceptionAdapter.java:L270"],
        findings: ["Preserve UiRequiredException and IntuneAppProtectionPolicyRequiredException subtypes during cached error replay."],
        questions: [],
    };

    const askedQuestions = [];
    const challengeCtx = context([round1Ev, round2Ev, challengePass, implementation(), review()]);
    challengeCtx.args = {
        prompt: "add throttling to common (AB#3802484)",
        options: {
            createPullRequests: false,
            iterativeIntake: true,
            challengeBrief: true,
        },
    };
    const challengePacket = await runCodeFeature(challengeCtx, {
        askUser: async (q) => {
            askedQuestions.push(q);
            return "30s fallback, 3600s max cap.";
        },
    });
    assert.equal(challengePacket.recommendation, "GO to PR Review");
    assert.equal(askedQuestions.length, 1);
    assert.ok(challengePacket.task.designSummary.includes("Preserve UiRequiredException"));
    const prBody = challengePacket.pullRequests[0].body;
    const parsedDelivery = parseMarkerBlock(prBody, deliveryDecisionMarkers);
    const parsedStatus = parseMarkerBlock(prBody, statusMarkers);
    assert.equal(parsedDelivery?.rollout?.tag, "feature-flag");
    assert.equal(parsedStatus?.status, currentStatuses.publishedForObservation);

    // 2. Resume worktree inspection (recoverWorktree) after builder blocker
    const blockedBuilder = {
        ...implementation(),
        blockingUnknowns: ["Manual setup required in worktree."],
    };
    const inspectPass = {
        verdict: "pass",
        summary: "Manual repair in worktree verified and in scope.",
        evidence: [
            "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt:L1-L40",
            "common4j/src/test/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCacheTest.kt:L1-L35",
        ],
        prTitle: "Add Retry-After throttle cache in Common",
        prSummary: "Implements Retry-After cache.",
        testPlan: ".\\gradlew.bat :common4j:test",
        rolloutAndFlag: "CommonFlight.ENABLE_RETRY_AFTER_THROTTLE",
    };
    const recoverPaused = [];
    const recoverCtx = context([evidence(), blockedBuilder, inspectPass, review()]);
    recoverCtx.pause = async (key) => {
        recoverPaused.push(key);
    };
    recoverCtx.args = {
        task: task(),
        options: {
            createPullRequests: false,
            pauseOnBlocker: true,
            enableRecoveryInspection: true,
        },
    };
    const recoveredPacket = await runCodeFeature(recoverCtx);
    assert.equal(recoveredPacket.recommendation, "GO to PR Review");
    assert.equal(recoverPaused.length, 1);

    // 3. Observer anti-loop (computeRepairIdentity) stops repeated identical repair on same commit
    const repeatObs = {
        verdict: "repair",
        repairKind: "ci-failure",
        summary: "Same CI failure on commit abc111.",
        evidence: ["check-run-1"],
        actionableFindings: ["Fix compile error in SilentRequestIdentity.kt"],
    };
    const repeatReviewFail = {
        verdict: "repair",
        summary: "Still broken.",
        evidence: ["SilentRequestIdentity.kt"],
        findings: ["Still missing explicit getter."],
    };
    const obsPauses = [];
    const obsCtx = context([
        repeatObs,
        {
            verdict: "pass",
            summary: "Attempted fix 1",
            evidence: ["SilentRequestIdentity.kt"],
            prTitle: "Fix 1",
            prSummary: "Fix 1",
            testPlan: "test",
            rolloutAndFlag: "flight",
        },
        repeatReviewFail,
        repeatObs,
    ]);
    obsCtx.pause = async (key) => {
        obsPauses.push(key);
    };
    obsCtx.args = {
        prUrl: "https://msft.ghe.com/security/ad-accounts-for-android/pull/362",
    };
    const obsLoopResult = await runPrObserver(obsCtx, {
        fetchPrSnapshot: () => ({
            number: 362,
            state: "OPEN",
            mergeStateStatus: "CLEAN",
            headRefName: "shjameel/broker-silent-server-throttling",
            headRefOid: "abc111",
            baseRefName: "dev",
        }),
        exec: () => "",
    });
    assert.equal(obsLoopResult.status, currentStatuses.needsOwnerDecision);
    assert.equal(obsPauses.length, 1);
    assert.ok(computeRepairIdentity({ observation: repeatObs, snapshot: { headRefOid: "abc111" }, worktreeSnap: "" }));

    // 4. ADO PR ?path= design doc URL parsing & authenticator target inference
    const parsedAdo = parseAdoDesignDocUrl(
        "https://dev.azure.com/IdentityDivision/DevEx/_git/AuthLibrariesApiReview/pullrequest/28487?path=/%5BAndroid%5D%20Backup%20%26%20Restore%20(v2)/Expanded%20Entra%20Guided%20Restore/expanded-entra-guided-restore-design.v2.md",
    );
    assert.equal(parsedAdo?.org, "IdentityDivision");
    assert.equal(parsedAdo?.project, "DevEx");
    assert.equal(parsedAdo?.repo, "AuthLibrariesApiReview");
    assert.equal(parsedAdo?.prId, 28487);
    assert.equal(
        parsedAdo?.itemPath,
        "/[Android] Backup & Restore (v2)/Expanded Entra Guided Restore/expanded-entra-guided-restore-design.v2.md",
    );
    const resolvedFromUrl = resolveInputSources(
        {
            designDocPath:
                "https://dev.azure.com/IdentityDivision/DevEx/_git/AuthLibrariesApiReview/pullrequest/28487?path=/%5BAndroid%5D%20Backup%20%26%20Restore%20(v2)/Expanded%20Entra%20Guided%20Restore/expanded-entra-guided-restore-design.v2.md",
        },
        {
            linkSubrepos: false,
            fetchDesignDoc: () =>
                "# [Android] Expanded Entra Guided Restore\n\n**Feature:** [3652306](https://identitydivision.visualstudio.com/Engineering/_workitems/edit/3652306)\n\n| Authenticator app | Entry routing |\nNo MSAL, Common, Broker, ADAL, OneAuth, or 1ES-Pipelines change is proposed.",
        },
    );
    const normalizedAuthAppTask = normalizeTask(resolvedFromUrl);
    assert.equal(normalizedAuthAppTask.inputMode, "design_doc");
    assert.equal(normalizedAuthAppTask.workItemId, 3652306);
    assert.equal(normalizedAuthAppTask.targets[0].repository, "authenticator");
    assert.equal(normalizedAuthAppTask.targets[0].baseBranch, "working");

    // 5. pr-observer automatically polls while CI is queued/in-progress without pausing or burning subagents
    const queuedSnapshot = {
        number: 17764690,
        state: "active",
        mergeStateStatus: "succeeded",
        headRefName: "users/shjameel/expanded-entra-guided-restore-3652306",
        headRefOid: "f13c4ef24",
        baseRefName: "working",
        statusCheckRollup: [
            { state: null, description: "Coverage status check queued for Android Authenticator PR Checks" },
            { state: null, description: "Ownership Enforcer: Missing Approvals" },
            { state: "succeeded", description: "Regional Approval Policy - Passed" },
        ],
        prBuilds: [
            {
                id: 188765080,
                definition: "Android Authenticator PR Checks",
                status: "inProgress",
                result: null,
                failedTasks: [],
            },
        ],
        activeReviewThreads: [],
    };
    const failedDetektSnapshot = {
        ...queuedSnapshot,
        prBuilds: [
            {
                id: 188765080,
                definition: "Android Authenticator PR Checks",
                status: "inProgress",
                result: null,
                failedTasks: [
                    {
                        name: "gradlew detektCheck",
                        issues: ["Error: gradlew failed with exit code 1"],
                        excerpt: ["EntraAddAccountViewModel.kt:298:9: The function createOperationContexts is too long (60). [LongMethod]"],
                    },
                ],
            },
        ],
    };
    assert.equal(isSnapshotWaitingForCiOnly(queuedSnapshot), true);
    assert.equal(isSnapshotWaitingForCiOnly(failedDetektSnapshot), false);

    let snapshotPollCount = 0;
    let sleepCalls = 0;
    const pollPaused = [];
    const pollCtx = context([]);
    pollCtx.pause = async (key) => {
        pollPaused.push(key);
    };
    pollCtx.args = {
        prUrl: "https://msazure.visualstudio.com/DefaultCollection/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/17764690",
        options: {
            ciPollIntervalMs: 0,
            maxCiPollAttempts: 12,
        },
    };
    const greenSnapshot = {
        ...queuedSnapshot,
        statusCheckRollup: [{ state: "succeeded", description: "Coverage status check succeeded" }],
        prBuilds: [{ id: 188765080, definition: "Android Authenticator PR Checks", status: "completed", result: "succeeded", failedTasks: [] }],
    };
    assert.equal(isSnapshotReadyForHumanMerge(queuedSnapshot), false);
    assert.equal(isSnapshotReadyForHumanMerge(failedDetektSnapshot), false);
    assert.equal(isSnapshotReadyForHumanMerge(greenSnapshot), true);

    const pollResult = await runPrObserver(pollCtx, {
        fetchPrSnapshot: () => {
            snapshotPollCount++;
            if (snapshotPollCount < 10) return queuedSnapshot;
            return greenSnapshot;
        },
        sleep: async () => {
            sleepCalls++;
        },
        exec: () => "",
    });
    assert.equal(pollResult.status, currentStatuses.readyForHumanMerge);
    assert.equal(snapshotPollCount, 10);
    assert.equal(sleepCalls, 9);
    assert.equal(pollPaused.length, 0);
    assert.equal(pollCtx.calls.length, 0);

    // 6. Protected CI pipeline & static-analysis config files are blocked and auto-reverted on feature PRs
    assert.equal(isProtectedPipelineOrQualityPath(".pipelines/AndroidPR.yml"), true);
    assert.equal(isProtectedPipelineOrQualityPath("PhoneFactor/code_quality_tools/configs/detekt.yml"), true);
    assert.equal(isProtectedPipelineOrQualityPath("PhoneFactor/app/src/main/java/Foo.kt"), false);
    assert.equal(isPipelineModificationAuthorized(normalizedAuthAppTask, normalizedAuthAppTask.targets[0]), false);

    const unauthorizedImpl = implementation();
    unauthorizedImpl.targets[0].files.push({
        path: ".pipelines/AndroidPR.yml",
        action: "modify",
        content: "steps: []\n",
        summary: "Weaken checkstyle publish condition",
    });
    const pipelineErrors = validateImplementation(unauthorizedImpl, task());
    assert.ok(pipelineErrors.some((e) => e.includes("modified protected CI pipeline or static-analysis config (.pipelines/AndroidPR.yml)")));

    const tempProtectRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-protect-"));
    try {
        mkdirSync(path.join(tempProtectRoot, ".git"), { recursive: true });
        const checkoutCalls = [];
        const reverted = revertUnauthorizedProtectedFiles(tempProtectRoot, {
            allowPipelineEdits: false,
            exec: (cmd, args) => {
                if (args.includes("status")) {
                    return " M .pipelines/AndroidPR.yml\n M PhoneFactor/app/src/main/java/Foo.kt";
                }
                if (args.includes("checkout")) {
                    checkoutCalls.push(args);
                }
                return "";
            },
        });
        assert.deepEqual(reverted, [".pipelines/AndroidPR.yml"]);
        assert.equal(checkoutCalls.length, 1);
    } finally {
        rmSync(tempProtectRoot, { recursive: true, force: true });
    }

    // 7. Natural prompt with both a PBI URL and a Design Doc URL scopes to the PBI while retaining design doc context
    const naturalResolved = resolveInputSources(
        {
            prompt:
                "Implement https://dev.azure.com/IdentityDivision/Engineering/_workitems/edit/3804404 using this design doc for additional context: https://dev.azure.com/IdentityDivision/DevEx/_git/AuthLibrariesApiReview/pullrequest/28487?path=/%5BAndroid%5D%20Backup%20%26%20Restore%20(v2)/Expanded%20Entra%20Guided%20Restore/expanded-entra-guided-restore-design.v2.md",
        },
        {
            linkSubrepos: false,
            fetchWorkItem: (id) => ({
                id,
                fields: {
                    "System.AreaPath": "Engineering\\Auth Client\\Android Authenticator App",
                    "System.Title": "[Entra Guided Restore] Make guided navigation marker-aware and lifecycle-safe",
                    "System.Description":
                        "<p>PBI-1 of Feature 3804403. Repository: AD-MFA-phonefactor-phoneApp-android; base working.</p><ul><li>Add required Boolean hadPasskeyOnOldDevice to GuidedRestoreActivity.newIntent.</li></ul>",
                },
                relations: [
                    {
                        rel: "System.LinkTypes.Hierarchy-Reverse",
                        url: "https://identitydivision.visualstudio.com/_apis/wit/workItems/3804403",
                    },
                ],
            }),
            fetchDesignDoc: () =>
                "# [Android] Expanded Entra Guided Restore\n\n**Feature:** [3652306](https://identitydivision.visualstudio.com/Engineering/_workitems/edit/3652306)\n\nFull multi-PBI design doc context.",
        },
    );
    const naturalTask = normalizeTask(naturalResolved);
    assert.equal(naturalTask.inputMode, "pbi");
    assert.equal(naturalTask.workItemId, 3804404);
    assert.equal(naturalTask.title, "[Entra Guided Restore] Make guided navigation marker-aware and lifecycle-safe");
    assert.equal(naturalTask.targets.length, 1);
    assert.equal(naturalTask.targets[0].repository, "authenticator");
    assert.equal(naturalTask.targets[0].baseBranch, "working");
    assert.equal(naturalTask.targets[0].workItemId, 3804404);
    assert.ok(naturalTask.designSummary.includes("SCOPED PBI IMPLEMENTATION TARGET"));
    assert.ok(naturalTask.designSummary.includes("SUPPORTING DESIGN DOCUMENT CONTEXT"));

    // 8. Natural prompt with "PBI 3804404" and a workflow PR link containing "Fixes AB#3805593" binds 3804404 and ignores PR link AB#
    const managerPrompt =
        "ok then implement the PBI 3804404 using the coding and PR dynamic workflow here [Add guarded Android coding and PR dynamic workflow, Fixes AB#3805593 by shahzaibj · Pull Request #482 · AzureAD/android-complete](https://github.com/AzureAD/android-complete/pull/482)";
    assert.deepEqual(extractWorkItemIdsFromText(managerPrompt), [3804404]);
    const managerResolved = resolveInputSources(
        { prompt: managerPrompt },
        {
            linkSubrepos: false,
            fetchWorkItem: (id) => ({
                id,
                fields: {
                    "System.AreaPath": "Engineering\\Auth Client\\Android Authenticator App",
                    "System.Title": "[Entra Guided Restore] Make guided navigation marker-aware and lifecycle-safe",
                    "System.Description":
                        "<p>PBI-1 of Feature 3804403. Repository: AD-MFA-phonefactor-phoneApp-android; base working.</p><ul><li>Add required Boolean hadPasskeyOnOldDevice to GuidedRestoreActivity.newIntent.</li></ul>",
                },
                relations: [],
            }),
        },
    );
    const managerTask = normalizeTask(managerResolved);
    assert.equal(managerTask.inputMode, "pbi");
    assert.equal(managerTask.workItemId, 3804404);
    assert.equal(managerTask.targets[0].workItemId, 3804404);
    assert.equal(managerTask.targets[0].repository, "authenticator");

    // 9. buildInspectedFilesContext pre-loads cited file excerpts with 1-based line numbers for code-builder
    const tempContextRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-context-"));
    try {
        const authFileDir = path.join(tempContextRoot, "authenticator", "PhoneFactor", "app", "src", "main");
        const commonNestedDir = path.join(tempContextRoot, "common", "common", "src", "main");
        mkdirSync(authFileDir, { recursive: true });
        mkdirSync(commonNestedDir, { recursive: true });
        writeFileSync(
            path.join(authFileDir, "GuidedRestoreActivity.kt"),
            ["package com.microsoft.authenticator", "", "class GuidedRestoreActivity {", "    fun newIntent() = Unit", "}"].join("\n"),
            "utf8",
        );
        writeFileSync(
            path.join(commonNestedDir, "CommonExample.kt"),
            ["package com.microsoft.identity.common", "class CommonExample"].join("\n"),
            "utf8",
        );
        const preloaded = buildInspectedFilesContext(tempContextRoot, managerTask, {
            references: [
                {
                    id: "E1",
                    repository: "authenticator",
                    revision: "HEAD",
                    path: "authenticator/PhoneFactor/app/src/main/GuidedRestoreActivity.kt:L1-L5",
                    locator: "PhoneFactor/app/src/main/GuidedRestoreActivity.kt:L1-L5 — add hadPasskeyOnOldDevice param",
                },
                {
                    id: "E2",
                    repository: "common",
                    revision: "HEAD",
                    path: "common/src/main/CommonExample.kt:L1-L2",
                    locator: "common/src/main/CommonExample.kt:L1-L2 — verify cross-repo reference and module-prefixed path",
                },
            ],
        });
        assert.equal(preloaded.length, 2);
        assert.equal(preloaded[0].repository, "authenticator");
        assert.equal(preloaded[0].path, "PhoneFactor/app/src/main/GuidedRestoreActivity.kt");
        assert.ok(preloaded[0].excerpt.includes("4:     fun newIntent() = Unit"));
        assert.equal(preloaded[1].repository, "common");
        assert.equal(preloaded[1].path, "common/src/main/CommonExample.kt");
        assert.ok(preloaded[1].excerpt.includes("2: class CommonExample"));
    } finally {
        rmSync(tempContextRoot, { recursive: true, force: true });
    }

    // 10. Reviews, top-level comments, uppercase FAILURE status contexts, and verified prChangedFiles
    assert.equal(
        isSnapshotReadyForHumanMerge({
            ...greenSnapshot,
            reviews: [{ author: { login: "reviewer1" }, state: "CHANGES_REQUESTED", body: "Please update error handling." }],
        }),
        false,
    );
    assert.equal(
        isSnapshotReadyForHumanMerge({
            ...greenSnapshot,
            reviews: [
                { author: { login: "reviewer1" }, state: "CHANGES_REQUESTED", body: "Please update error handling." },
                { author: { login: "reviewer1" }, state: "COMMENTED", body: "One more nit before approval." },
                { author: { login: "reviewer1" }, state: "APPROVED", body: "LGTM!" },
            ],
        }),
        true,
    );
    assert.equal(
        isSnapshotReadyForHumanMerge({
            ...greenSnapshot,
            comments: [{ author: { login: "reviewer1" }, body: "Can we also cover the null case?" }],
        }),
        false,
    );
    assert.equal(
        hasActionableReviewOrCommentFeedback({
            comments: [{ author: { login: "github-actions" }, body: "<!-- code: lcc-200 -->" }],
            reviews: [{ author: { login: "copilot-pull-request-reviewer" }, state: "COMMENTED", body: "<!-- ccr-overview-v2 -->" }],
        }),
        false,
    );
    assert.equal(
        isSnapshotWaitingForCiOnly({
            ...queuedSnapshot,
            statusCheckRollup: [
                { state: "FAILURE", description: "StatusContext check failed" },
                { state: "PENDING", description: "Another check running" },
            ],
        }),
        false,
    );
    assert.deepEqual(
        listPrChangedFiles("", { files: [{ path: "/PhoneFactor/app/Foo.kt" }] }),
        ["PhoneFactor/app/Foo.kt"],
    );
    assert.equal(listPrChangedFiles("", { baseRefName: "dev" }), null);

    // 11. Windows Path/PATH normalization, cmd.exe resolution, lint deferral, and on-disk git diff rendering
    const mixedCaseEnv = buildNormalizedProcessEnv(
        {
            SystemRoot: "C:\\Windows",
            Path: "C:\\Program Files\\Git\\cmd;C:\\Windows\\System32",
            GH_TOKEN: "secret-gh-token",
            ENV_VSTS_MVN_CRED_ACCESSTOKEN: "secret-vsts-token",
        },
        { JAVA_HOME: "C:\\Program Files\\Android\\Android Studio\\jbr" },
        {
            stripKeys: ["GH_TOKEN", "ENV_VSTS_MVN_CRED_ACCESSTOKEN"],
            prependPathDirs: ["C:\\Program Files\\Android\\Android Studio\\jbr\\bin"],
            includeStandardWindowsToolDirs: false,
        },
    );
    assert.equal(mixedCaseEnv.Path, undefined);
    assert.equal(mixedCaseEnv.GH_TOKEN, undefined);
    assert.equal(mixedCaseEnv.ENV_VSTS_MVN_CRED_ACCESSTOKEN, undefined);
    assert.ok(mixedCaseEnv.PATH.includes("C:\\Program Files\\Android\\Android Studio\\jbr\\bin"));
    assert.ok(mixedCaseEnv.PATH.includes("C:\\Program Files\\Git\\cmd"));
    assert.ok(resolveWindowsCmdExecutable({ SystemRoot: "..\\relative" }).toLowerCase().endsWith("cmd.exe"));

    const lintCombined = parseAllowedGradleVerificationCommand(
        ".\\gradlew.bat :app:detektCheck :app:lintProductionDebug -x lint",
    );
    assert.deepEqual(lintCombined?.args, [":app:detektCheck", "-x", "lint"]);
    const lintOnly = parseAllowedGradleVerificationCommand(".\\gradlew.bat :app:lintProductionDebug");
    assert.equal(lintOnly?.deferredToCi, true);

    const normalizedEv = normalizeEvidenceResponse(
        {
            verdict: "ready",
            summary: "Ready",
            repository: "authenticator",
            baseBranch: "working",
            workItemId: 3804404,
            requirements: ["R1: Add marker"],
            verificationCommands: [
                "From the isolated authenticator checkout: git rev-parse HEAD; git status --short",
                "From authenticator/PhoneFactor: .\\gradlew.bat :app:compileProductionDebugKotlin",
            ],
            questions: [],
            evidence: ["GuidedRestoreActivity.kt:L1-L20"],
        },
        managerTask,
    );
    assert.equal(normalizedEv.extractedSpec.targets[0].verificationCommands.length, 1);
    assert.ok(normalizedEv.extractedSpec.targets[0].verificationCommands[0].includes("compileProductionDebugKotlin"));

    const slimRev = normalizeReviewResponse(
        {
            verdict: "pass",
            summary: "All good",
            evidence: ["A".repeat(2000), "B".repeat(2000)],
            findings: [],
        },
        managerTask,
    );
    assert.ok(slimRev.acceptanceCriteria[0].evidence.length < 100);

    const tempDiffRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-diff-"));
    try {
        mkdirSync(path.join(tempDiffRoot, ".git"), { recursive: true });
        writeFileSync(path.join(tempDiffRoot, "Foo.kt"), "package test\r\nclass Foo\r\n", "utf8");
        const backups = new Map();
        const files = [{ path: "Foo.kt", action: "modify", content: "", summary: "Updated Foo" }];
        applyTargetFiles(tempDiffRoot, files, backups);
        assert.equal(files[0].content, "");
        assert.equal(readFileSync(path.join(tempDiffRoot, "Foo.kt"), "utf8"), "package test\nclass Foo\n");
        const rendered = renderUnifiedDiff(backups, files, {
            repoRoot: tempDiffRoot,
            exec: (cmd, args) => {
                if (cmd === "git" && args.includes("diff")) return "diff --git a/Foo.kt b/Foo.kt\n+class Foo";
                return "";
            },
        });
        assert.equal(rendered, "diff --git a/Foo.kt b/Foo.kt\n+class Foo");
        const restoreCalls = [];
        restoreTargetFiles(tempDiffRoot, backups, {
            exec: (cmd, args) => {
                restoreCalls.push({ cmd, args });
                return "";
            },
        });
        assert.equal(restoreCalls.length, 1);
        assert.deepEqual(restoreCalls[0].args, ["checkout", "HEAD", "--", "Foo.kt"]);

        // Pre-builder dirty file baseline preserves human edits on rollback instead of resetting to HEAD
        writeFileSync(path.join(tempDiffRoot, "Dirty.kt"), "package test\n// human edit\n", "utf8");
        const dirtyBackups = new Map();
        capturePreBuilderBaselineBackups(tempDiffRoot, dirtyBackups, {
            exec: () => " M Dirty.kt",
        });
        writeFileSync(path.join(tempDiffRoot, "Dirty.kt"), "package test\n// builder overwrite\n", "utf8");
        applyTargetFiles(
            tempDiffRoot,
            [{ path: "Dirty.kt", action: "modify", content: "", summary: "Builder modified Dirty.kt" }],
            dirtyBackups,
        );
        restoreTargetFiles(tempDiffRoot, dirtyBackups, {
            exec: () => {
                throw new Error("Should restore from captured bytes, not git checkout HEAD");
            },
        });
        assert.equal(readFileSync(path.join(tempDiffRoot, "Dirty.kt"), "utf8"), "package test\n// human edit\n");
    } finally {
        rmSync(tempDiffRoot, { recursive: true, force: true });
    }

    // 12. Stage 0 prerequisite check + interactive human recovery + XML build receipts + in-process Kotlin flag
    const inProcessCmd = parseAllowedGradleVerificationCommand(
        ".\\gradlew.bat :app:compileProductionDebugKotlin -Pkotlin.compiler.execution.strategy=in-process --no-daemon",
    );
    assert.deepEqual(inProcessCmd?.args, [
        ":app:compileProductionDebugKotlin",
        "-Pkotlin.compiler.execution.strategy=in-process",
        "--no-daemon",
    ]);
    assert.equal(
        isDefinitiveCodeOrTestFailure(
            "> Task :app:compileProductionDebugKotlin FAILED\nCaused by: java.net.UnknownHostException: msazure.pkgs.visualstudio.com",
        ),
        false,
    );
    assert.equal(
        isDefinitiveCodeOrTestFailure(
            "> Task :app:compileProductionDebugKotlin FAILED\nUnresolved reference: FooBar\n> Task :other:compile FAILED\nCaused by: java.net.UnknownHostException: msazure.pkgs.visualstudio.com",
        ),
        true,
    );
    assert.equal(
        isInfrastructureGateFailure(
            "FAILURE: Build failed with an exception. > A problem occurred starting process 'command 'git''",
        ),
        true,
    );
    assert.equal(isInfrastructureGateFailure("Unresolved reference: FooBar"), false);

    const tempPrereqRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-prereq-"));
    try {
        const pfDir = path.join(tempPrereqRoot, "authenticator", "PhoneFactor");
        const fakeJdk21 = path.join(tempPrereqRoot, "jdk-21");
        const fakeJdk21Bin = path.join(fakeJdk21, "bin");
        const fakeSdk = path.join(tempPrereqRoot, "android-sdk");
        const xmlDir = path.join(pfDir, "app", "build", "test-results", "testProductionDebugUnitTest");
        mkdirSync(pfDir, { recursive: true });
        mkdirSync(fakeJdk21Bin, { recursive: true });
        mkdirSync(fakeSdk, { recursive: true });
        mkdirSync(xmlDir, { recursive: true });
        writeFileSync(path.join(pfDir, "gradlew.bat"), "@echo off\r\n", "utf8");
        writeFileSync(path.join(pfDir, "local.properties"), "ndk.dir=/opt/android-ndk\nsdk.dir=/nonexistent/stale/sdk\n", "utf8");
        writeFileSync(path.join(fakeJdk21, "release"), 'JAVA_VERSION="21.0.4"\n', "utf8");
        writeFileSync(path.join(fakeJdk21Bin, process.platform === "win32" ? "java.exe" : "java"), "", "utf8");
        writeFileSync(path.join(fakeJdk21Bin, process.platform === "win32" ? "javac.exe" : "javac"), "", "utf8");
        writeFileSync(
            path.join(xmlDir, "TEST-EntraRestoreEntryPointCoordinatorTest.xml"),
            '<testsuite name="EntraRestoreEntryPointCoordinatorTest" tests="13" failures="0" errors="0"/>',
            "utf8",
        );

        const receipt = collectBuildReceiptOnDisk(path.join(tempPrereqRoot, "authenticator"), {
            javaHome: fakeJdk21,
            exec: () => "clean",
        });
        assert.equal(receipt.verified, true);
        assert.equal(receipt.testsRun, 13);
        assert.equal(receipt.testsFailed, 0);

        const testEnv = { JAVA_HOME: "", PATH: fakeJdk21Bin, ANDROID_HOME: "/nonexistent/android/home", ANDROID_SDK_ROOT: "" };
        let azLoggedIn = false;
        const prereqFail = verifyWorkflowPrerequisites({
            workspaceRoot: tempPrereqRoot,
            targets: managerTask.targets,
            createPullRequests: true,
            env: testEnv,
            exec: (cmd) => {
                if (cmd === "git") return "git version 2.47.0";
                if (cmd === "az") {
                    if (!azLoggedIn) throw new Error("Please run 'az login'");
                    return "valid-ado-token";
                }
                return "";
            },
        });
        assert.equal(prereqFail.ok, false);
        assert.ok(!prereqFail.questions.some((q) => q.includes("JDK 21")));
        assert.ok(prereqFail.questions.some((q) => q.includes("Android SDK")));
        assert.ok(prereqFail.questions.some((q) => q.includes("az login")));

        applyPrerequisiteAnswers(
            [
                { question: prereqFail.questions.find((q) => q.includes("Android SDK")), answer: fakeSdk },
                { question: prereqFail.questions.find((q) => q.includes("az login")), answer: "ready" },
            ],
            { workspaceRoot: tempPrereqRoot, targets: managerTask.targets, env: testEnv },
        );
        azLoggedIn = true;
        const updatedLocalProps = readFileSync(path.join(pfDir, "local.properties"), "utf8");
        assert.ok(updatedLocalProps.includes("ndk.dir=/opt/android-ndk"));

        const prereqPass = verifyWorkflowPrerequisites({
            workspaceRoot: tempPrereqRoot,
            targets: managerTask.targets,
            createPullRequests: true,
            env: testEnv,
            exec: (cmd) => {
                if (cmd === "git") return "git version 2.47.0";
                if (cmd === "az") return "valid-ado-token";
                return "";
            },
        });
        assert.equal(prereqPass.ok, true);
        const preflightAfter = runTargetEnvironmentPreflight(tempPrereqRoot, managerTask.targets[0], testEnv);
        assert.equal(preflightAfter.sdkConfigured, true);
        assert.equal(preflightAfter.javaMajorVersion, 21);

        // 13. Fail-forward / short-circuit-to-CI when local Gradle times out or hits non-code errors
        assert.equal(isDefinitiveCodeOrTestFailure("> Task :app:compileProductionDebugKotlin FAILED\nUnresolved reference: foo"), true);
        assert.equal(isDefinitiveCodeOrTestFailure("Command failed: spawnSync cmd.exe ETIMEDOUT"), false);
        assert.equal(
            isBuilderShortCircuitedToCi({
                verdict: "repair",
                summary: "The bounded retry was stopped approaching 12 minutes; compilation pending CI.",
                testPlan: "Local Gradle verification was short-circuited to CI.",
                evidence: [],
            }),
            true,
        );

        let timeoutGateCalls = 0;
        const scImpl = implementation();
        const scGate = runLocalVerificationGate({
            repoRoot: path.join(tempPrereqRoot, "authenticator"),
            target: {
                id: "pbi-3804404",
                repository: "authenticator",
                verificationCommands: [
                    ".\\gradlew.bat :app:compileProductionDebugKotlin",
                    ".\\gradlew.bat :app:testProductionDebugUnitTest --tests *CoordinatorTest",
                ],
            },
            targetImpl: scImpl.targets[0],
            implementation: scImpl,
            exec: () => {
                timeoutGateCalls++;
                throw new Error("Command failed: spawnSync cmd.exe ETIMEDOUT");
            },
        });
        assert.equal(timeoutGateCalls, 1);
        assert.deepEqual(scGate.errors, []);
        assert.equal(scGate.deferredNotes.length, 1);
        assert.ok(scGate.deferredNotes[0].includes("short-circuited to CI"));
    } finally {
        rmSync(tempPrereqRoot, { recursive: true, force: true });
    }
});

test("Detekt weighted-issues summaries remain blocking, including mixed infrastructure failures", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-detekt-"));
    try {
        for (const diagnostic of [
            "Analysis failed with 1 weighted issues.",
            "Analysis failed with 12 weighted issues.\nCaused by: java.net.UnknownHostException: feed.invalid",
        ]) {
            const gate = runLocalVerificationGate({
                repoRoot: root,
                target: { id: "common", verificationCommands: ["./gradlew :common4j:detekt"] },
                exec: () => { throw new Error(diagnostic); },
            });
            assert.equal(gate.errors.length, 1);
            assert.equal(gate.outcomes.failed.length, 1);
            assert.deepEqual(gate.deferredNotes, []);
        }
        assert.equal(isDefinitiveCodeOrTestFailure("Analysis completed with 0 weighted issues."), false);
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

test("JDK prerequisites reject invalid explicit homes and accept supported PATH installations", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-jdk-"));
    try {
        const home = path.join(root, "jdk");
        const bin = path.join(home, "bin");
        mkdirSync(bin, { recursive: true });
        const target = { id: "auth", repository: "authenticator", localPath: "." };
        const env = { JAVA_HOME: home, PATH: "", ANDROID_HOME: "", ANDROID_SDK_ROOT: "" };
        const check = () => verifyWorkflowPrerequisites({
            workspaceRoot: root, targets: [target], env,
            exec: () => "fixture-result",
        });
        assert.equal(resolveTargetJavaHome(target, env), "");
        assert.ok(check().questions.some((q) => q.includes("requires JDK 21")));
        writeFileSync(path.join(home, "release"), 'JAVA_VERSION="21.0.4"\n');
        assert.equal(resolveTargetJavaHome(target, env), "");
        for (const tool of ["java", "javac"]) {
            writeFileSync(path.join(bin, process.platform === "win32" ? `${tool}.exe` : tool), "");
        }
        assert.equal(resolveTargetJavaHome(target, env), home);
        assert.ok(!check().questions.some((q) => q.includes("requires JDK 21")));
        assert.equal(resolveTargetJavaHome(target, { ...env, JAVA_HOME: "", PATH: bin }), home);
        for (const version of ["17.0.1", "25.0.1", "invalid"]) {
            writeFileSync(path.join(home, "release"), `JAVA_VERSION="${version}"\n`);
            assert.equal(resolveTargetJavaHome(target, env), "");
            assert.ok(check().questions.some((q) => q.includes("requires JDK 21")));
        }
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

test("JDK recovery reports the configured home and uses the approved alternative for the run", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-jdk-recovery-"));
    try {
        const oldJdk = path.join(root, "jdk8");
        const newJdk = path.join(root, "jdk21");
        const sdk = path.join(root, "sdk");
        const repo = path.join(root, "common");
        for (const [home, version] of [[oldJdk, "1.8.0"], [newJdk, "21.0.4"]]) {
            mkdirSync(path.join(home, "bin"), { recursive: true });
            writeFileSync(path.join(home, "release"), `JAVA_VERSION="${version}"\n`);
            for (const tool of ["java", "javac"]) {
                writeFileSync(path.join(home, "bin", process.platform === "win32" ? `${tool}.exe` : tool), "");
            }
        }
        mkdirSync(sdk);
        mkdirSync(repo);
        writeFileSync(path.join(repo, "gradlew.bat"), "");
        const env = { ...process.env, JAVA_HOME: oldJdk, PATH: path.join(newJdk, "bin"), ANDROID_HOME: sdk };
        const target = { repository: "common", localPath: "common", verificationCommands: ["./gradlew :common4j:test"] };
        const exec = () => "ok";
        const failed = verifyWorkflowPrerequisites({ workspaceRoot: root, targets: [target], env, exec });
        const question = failed.questions.find((item) => item.includes("JDK"));
        assert.ok(question.includes(oldJdk));
        assert.match(question, /JDK 8/);
        assert.ok(question.includes(newJdk));
        assert.ok(!question.includes("JAVA_HOME: unset"));

        const unchanged = { ...env };
        applyPrerequisiteAnswers([{ question, answer: path.join(root, "missing") }], {
            workspaceRoot: root, targets: [target], env: unchanged,
        });
        assert.equal(unchanged.JAVA_HOME, oldJdk);
        assert.equal(verifyWorkflowPrerequisites({ workspaceRoot: root, targets: [target], env: unchanged, exec }).ok, false);
        applyPrerequisiteAnswers([{ question: "Repository 'common' requires JDK 17 or 21.", answer: "yes" }], {
            workspaceRoot: root, targets: [target], env: unchanged,
        });
        assert.equal(unchanged.JAVA_HOME, oldJdk);

        const originalHostHome = process.env.JAVA_HOME;
        const runTask = task();
        runTask.targets[0].verificationCommands = target.verificationCommands;
        const ctx = context([evidence(), implementation(), review()], runTask, {
            workspaceRoot: root, verifyPrerequisites: true, runLocalVerification: true,
        });
        let capturedGateEnv;
        const result = await runCodeFeature(ctx, {
            environment: env,
            askUser: async (prompt) => {
                assert.ok(prompt.includes(newJdk));
                return "yes";
            },
            exec: (command, _args, options) => {
                if (/gradlew/i.test(command)) capturedGateEnv = options.env;
                return "ok";
            },
        });
        assert.equal(result.recommendation, "GO to PR Review");
        assert.ok(ctx.calls.find((call) => call.options.agent === BUILDER_AGENT).prompt.includes(newJdk));
        assert.equal(capturedGateEnv.JAVA_HOME, newJdk);
        assert.equal(env.JAVA_HOME, oldJdk);
        assert.equal(process.env.JAVA_HOME, originalHostHome);

        const manual = { ...env };
        applyPrerequisiteAnswers([{ question, answer: newJdk }], {
            workspaceRoot: root, targets: [target], env: manual,
        });
        assert.equal(manual.JAVA_HOME, newJdk);
        rmSync(path.join(newJdk, "bin", process.platform === "win32" ? "javac.exe" : "javac"));
        const disappeared = { ...env };
        applyPrerequisiteAnswers([{ question, answer: "yes" }], {
            workspaceRoot: root, targets: [target], env: disappeared,
        });
        assert.equal(disappeared.JAVA_HOME, oldJdk);
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

function prerequisiteResumeFixture() {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-resume-"));
    const javaHome = path.join(root, "jdk21");
    const sdk = path.join(root, "sdk");
    mkdirSync(path.join(javaHome, "bin"), { recursive: true });
    writeFileSync(path.join(javaHome, "release"), 'JAVA_VERSION="21.0.4"\n');
    for (const tool of ["java", "javac"]) {
        writeFileSync(path.join(javaHome, "bin", process.platform === "win32" ? `${tool}.exe` : tool), "");
    }
    mkdirSync(sdk);
    mkdirSync(path.join(root, "common"));
    writeFileSync(path.join(root, "common", "gradlew.bat"), "");
    const env = { ...process.env, JAVA_HOME: path.join(root, "missing-jdk"), ANDROID_HOME: sdk };
    const journal = new Map();
    const checkpoints = new Set();
    const resumedTask = task();
    resumedTask.targets[0].verificationCommands = ["./gradlew :common4j:test"];
    const contexts = [];
    const makeContext = (results) => {
        const ctx = context(results, resumedTask, {
            workspaceRoot: root, verifyPrerequisites: true, runLocalVerification: true,
            createPullRequests: false, allowLocalOnly: true,
        });
        ctx.runId = "fixture-resume";
        ctx.session = { ui: { elicitation: async () => ({ action: "cancel" }) } };
        ctx.step = async (key, producer, options) => {
            if (options?.volatile) return producer();
            if (!journal.has(key)) journal.set(key, await producer());
            return journal.get(key);
        };
        ctx.pause = async (key) => {
            if (checkpoints.has(key)) return;
            checkpoints.add(key);
            throw Object.assign(new Error("Fixture durable checkpoint"), { name: "AbortError" });
        };
        contexts.push(ctx);
        return ctx;
    };
    const gateEnvs = [];
    const dependencies = {
        environment: env,
        exec: (command, _args, options) => {
            if (/gradlew/i.test(command)) gateEnvs.push(options.env);
            return "ok";
        },
    };
    const writeRecovery = (value = { runId: "fixture-resume", javaHome }) => {
        const file = prerequisiteRecoveryPath(root, "fixture-resume");
        mkdirSync(path.dirname(file), { recursive: true });
        writeFileSync(file, JSON.stringify(value));
    };
    return { root, javaHome, env, journal, checkpoints, contexts, makeContext, dependencies, gateEnvs, writeRecovery };
}

test("paused prerequisites recheck approved run-scoped recovery on a fresh attempt and replay without duplicate agents", async () => {
    const f = prerequisiteResumeFixture();
    const hostJavaHome = process.env.JAVA_HOME;
    try {
        const first = f.makeContext([]);
        first.session = { ui: { elicitation: async () => ({ action: "cancel" }) } };
        first.args.options.allowLocalOnly = true;
        await assert.rejects(runCodeFeature(first, f.dependencies), { name: "AbortError" });
        assert.equal(first.calls.length, 0);
        assert.equal(f.checkpoints.size, 1);
        assert.ok(first.logs.some((line) => line.includes(prerequisiteRecoveryPath(f.root, first.runId))));

        f.writeRecovery();
        const resumed = f.makeContext([evidence(), implementation(), review()]);
        const result = await runCodeFeature(resumed, f.dependencies);
        assert.equal(result.recommendation, "GO to PR Review");
        assert.equal(resumed.calls.length, 3);
        assert.ok(resumed.calls.find((call) => call.options.agent === BUILDER_AGENT).prompt.includes(f.javaHome));
        assert.equal(f.gateEnvs.at(-1).JAVA_HOME, f.javaHome);
        assert.equal(f.env.JAVA_HOME.endsWith("missing-jdk"), true);
        assert.equal(process.env.JAVA_HOME, hostJavaHome);

        const replay = f.makeContext([]);
        assert.equal((await runCodeFeature(replay, f.dependencies)).recommendation, "GO to PR Review");
        assert.equal(replay.calls.length, 0);
    } finally {
        rmSync(f.root, { recursive: true, force: true });
    }
});

test("inline prerequisite approval survives a later durable pause without asking again", async () => {
    const f = prerequisiteResumeFixture();
    let questions = 0;
    try {
        const first = f.makeContext([evidence()]);
        const step = first.step;
        first.step = async (key, producer, options) => {
            if (key.endsWith("-build-0")) await first.pause("fixture-after-evidence");
            return step(key, producer, options);
        };
        const dependencies = {
            ...f.dependencies,
            askUser: async () => { questions++; return f.javaHome; },
        };
        await assert.rejects(runCodeFeature(first, dependencies), { name: "AbortError" });
        const resumed = f.makeContext([implementation(), review()]);
        assert.equal((await runCodeFeature(resumed, dependencies)).recommendation, "GO to PR Review");
        assert.equal(questions, 1);
        assert.equal(resumed.calls.length, 2);
        assert.equal(f.gateEnvs.at(-1).JAVA_HOME, f.javaHome);
    } finally {
        rmSync(f.root, { recursive: true, force: true });
    }
});

test("preapproved run-scoped JDK supports unattended runs without changing host JAVA_HOME", async () => {
    const f = prerequisiteResumeFixture();
    const original = process.env.JAVA_HOME;
    try {
        const context = f.makeContext([evidence(), implementation(), review()]);
        context.args.options.javaHome = f.javaHome;
        const result = await runCodeFeature(context, {
            ...f.dependencies,
            askUser: async () => { throw new Error("Preapproved JDK must not prompt again"); },
        });
        assert.equal(result.recommendation, "GO to PR Review");
        assert.equal(f.gateEnvs.at(-1).JAVA_HOME, f.javaHome);
        assert.equal(process.env.JAVA_HOME, original);
    } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test("unresolved or invalid prerequisite recovery never advances to agents", async () => {
    for (const recovery of [
        null,
        { runId: "another-run", javaHome: "unused" },
        { runId: "fixture-resume", javaHome: "missing" },
        { runId: "fixture-resume", javaHome: "unused", token: "not-an-accepted-field" },
    ]) {
        const f = prerequisiteResumeFixture();
        try {
            await assert.rejects(runCodeFeature(f.makeContext([]), f.dependencies), { name: "AbortError" });
            if (recovery) {
                f.writeRecovery(recovery);
                await assert.rejects(runCodeFeature(f.makeContext([]), f.dependencies), /Invalid .*recovery file/);
            } else {
                await assert.rejects(runCodeFeature(f.makeContext([]), f.dependencies), { name: "AbortError" });
                assert.equal((await runCodeFeature(f.makeContext([]), f.dependencies)).recommendation, "needs clarification");
                assert.equal(f.checkpoints.size, 2);
            }
            assert.equal(f.contexts.flatMap((ctx) => ctx.calls).length, 0);
        } finally {
            rmSync(f.root, { recursive: true, force: true });
        }
    }
    assert.throws(() => prerequisiteRecoveryPath(os.tmpdir(), "..\\escape"), /Invalid workflow run ID/);
});

test("clarification cancellation and runtime failures propagate without becoming a prerequisite pause", async () => {
    for (const name of ["AbortError", "ConnectionError", "ResponseError"]) {
        const f = prerequisiteResumeFixture();
        try {
            const ctx = f.makeContext([]);
            ctx.args.options.allowLocalOnly = true;
            ctx.session = { ui: { elicitation: async () => { throw Object.assign(new Error("Fixture runtime failure"), { name }); } } };
            await assert.rejects(runCodeFeature(ctx, f.dependencies), { name });
            assert.equal(f.checkpoints.size, 0);
            assert.equal(ctx.calls.length, 0);
        } finally {
            rmSync(f.root, { recursive: true, force: true });
        }
    }
});

test("interrupted review preserves builder edits instead of treating cancellation as a failed review", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-interrupted-review-"));
    try {
        mkdirSync(path.join(root, "common"));
        const ctx = context([evidence(), implementation()], task(), {
            workspaceRoot: root, applyToWorkspace: true,
        });
        const agent = ctx.agent;
        ctx.agent = async (prompt, options) => {
            if (options.agent === REVIEWER_AGENT) {
                throw Object.assign(new Error("Fixture review interrupted"), { name: "AbortError" });
            }
            return agent(prompt, options);
        };
        await assert.rejects(runCodeFeature(ctx, { exec: () => "" }), { name: "AbortError" });
        const editedFile = path.join(root, "common", implementation().targets[0].files[0].path);
        assert.equal(readFileSync(editedFile, "utf8"), implementation().targets[0].files[0].content);
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

test("PowerShell builder example passes the compiler property as one native argument", {
    skip: process.platform !== "win32" && "Windows PowerShell argument fixture",
}, () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-powershell-args-"));
    try {
        writeFileSync(path.join(root, "dump-args.cjs"), "console.log(JSON.stringify(process.argv.slice(2)));\n");
        writeFileSync(path.join(root, "gradlew.bat"), `@echo off\r\n"${process.execPath}" "%~dp0dump-args.cjs" %*\r\n`);
        const builder = customAgents.find((item) => item.name === BUILDER_AGENT);
        assert.ok(builder.prompt.includes("'-Pkotlin.compiler.execution.strategy=in-process'"));
        const output = execFileSync("powershell", [
            "-NoProfile", "-Command", ".\\gradlew.bat '-Pkotlin.compiler.execution.strategy=in-process'",
        ], { cwd: root, encoding: "utf8" });
        assert.deepEqual(JSON.parse(output.trim()), ["-Pkotlin.compiler.execution.strategy=in-process"]);
        const unquoted = execFileSync("powershell", [
            "-NoProfile", "-Command", ".\\gradlew.bat -Pkotlin.compiler.execution.strategy=in-process",
        ], { cwd: root, encoding: "utf8" });
        assert.notDeepEqual(JSON.parse(unquoted.trim()), ["-Pkotlin.compiler.execution.strategy=in-process"]);
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

test("slim normalized reviews retain successful citations once in the final review packet", () => {
    const citations = ["Foo.kt:L10-L20 verified the null guard", "FooTest.kt:L25-L40 covers missing input"];
    const normalized = normalizeReviewResponse({
        verdict: "pass", summary: "Reviewed the null-input fix.", evidence: citations, findings: [],
    }, task());
    assert.equal(normalized.summary, "Reviewed the null-input fix.");
    assert.deepEqual(normalized.evidence, citations);
    assert.ok(validateReview(normalized, task()));
    for (const citation of citations) {
        assert.equal(JSON.stringify(normalized).split(citation).length - 1, 1);
    }
});

test("baseline rollback skips nested submodule directories and restores remaining dirty files", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "code-feature-submodule-"));
    try {
        mkdirSync(path.join(root, ".git"));
        mkdirSync(path.join(root, "common"));
        writeFileSync(path.join(root, "common", "keep.txt"), "nested work");
        writeFileSync(path.join(root, "Foo.kt"), "human edit");
        const backups = new Map();
        capturePreBuilderBaselineBackups(root, backups, {
            exec: () => " M common\n M Foo.kt\n D Deleted.kt",
        });
        assert.equal(backups.has("common"), false);
        assert.equal(backups.has("Foo.kt"), true);
        assert.equal(backups.get("Deleted.kt").exists, false);
        writeFileSync(path.join(root, "Foo.kt"), "builder edit");
        writeFileSync(path.join(root, "Deleted.kt"), "builder recreated");
        restoreTargetFiles(root, backups);
        assert.equal(readFileSync(path.join(root, "Foo.kt"), "utf8"), "human edit");
        assert.equal(readFileSync(path.join(root, "common", "keep.txt"), "utf8"), "nested work");
        assert.equal(existsSync(path.join(root, "Deleted.kt")), false);
    } finally {
        rmSync(root, { recursive: true, force: true });
    }
});

test("ADO publication script reuses an exact active PR on retry and recovers a lost create response", {
    skip: process.platform !== "win32" && "Windows PowerShell production-runner fixture",
}, () => {
    const payload = {
        sourceRefName: "refs/heads/users/test/feature",
        targetRefName: "refs/heads/working",
        title: "Fixture PR",
        description: "Fixture only",
        isDraft: true,
        labels: [],
    };
    const script = buildAdoPullRequestPublicationScript("https://ado.invalid/project/_apis/git/repositories/repo/pullRequests", payload);
    for (const loseResponse of [false, true]) {
        const fixture = `
function az { $global:LASTEXITCODE = 0; 'fixture-token' }
$script:posts = 0
$script:gets = 0
$script:created = $null
function Invoke-RestMethod {
    param($Uri, $Headers, $Method, $Body)
    if ($Method -eq 'Get') {
        $script:gets++
        if ($Uri -notmatch 'searchCriteria.sourceRefName=refs%2Fheads%2Fusers%2Ftest%2Ffeature' -or $Uri -notmatch 'searchCriteria.targetRefName=refs%2Fheads%2Fworking') { throw 'Missing exact branch filters' }
        $others = @(
            [pscustomobject]@{ pullRequestId=7; status='abandoned'; sourceRefName='refs/heads/users/test/feature'; targetRefName='refs/heads/working' },
            [pscustomobject]@{ pullRequestId=8; status='active'; sourceRefName='refs/heads/users/test/feature'; targetRefName='refs/heads/other' }
        )
        if ($script:created) { $others += $script:created }
        return @{ value=$others }
    }
    if ($Method -ne 'Post') { throw 'Unexpected mutation' }
    $script:posts++
    $script:created = [System.Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
    $script:created | Add-Member -NotePropertyName pullRequestId -NotePropertyValue 123
    $script:created | Add-Member -NotePropertyName status -NotePropertyValue active
    ${loseResponse ? "throw 'Lost POST response'" : "return $script:created"}
}
$first = & { ${script} }
$second = & { ${script} }
@{ first=$first; second=$second; posts=$script:posts; gets=$script:gets } | ConvertTo-Json -Compress
`;
        const result = JSON.parse(execFileSync("powershell", [
            "-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(fixture, "utf16le").toString("base64"),
        ], { encoding: "utf8", timeout: 30_000 }));
        assert.equal(result.first, 123);
        assert.equal(result.second, 123);
        assert.equal(result.posts, 1);
        assert.equal(result.gets, loseResponse ? 3 : 2);
    }
    const failureFixture = `
function az { $global:LASTEXITCODE = 0; 'fixture-token' }
function Invoke-RestMethod {
    param($Uri, $Headers, $Method, $Body)
    if ($Method -eq 'Get') { return @{ value=@() } }
    throw 'Creation failed without a matching PR'
}
${script}
`;
    assert.throws(() => execFileSync("powershell", [
        "-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(failureFixture, "utf16le").toString("base64"),
    ], { encoding: "utf8", timeout: 30_000, stdio: ["ignore", "pipe", "pipe"] }), /Creation failed/);
});
