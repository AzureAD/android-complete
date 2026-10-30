// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
    OBSERVER_AGENT,
    computeRepairIdentity,
    observerCustomAgent,
    observerWorkflowDefinition,
    parsePullRequestUrl,
    runPrObserver,
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
    customAgents,
    discoverChangedFilesFromGit,
    ensureIsolatedTargetWorktree,
    extractRunnableVerificationCommand,
    formatPrBody,
    formatPrTitle,
    isSafeRelativeFilePath,
    normalizeTask,
    parseAdoDesignDocUrl,
    resolveInputSources,
    resolveRoleSkillGuides,
    rubric,
    runCodeFeature,
    runLocalVerificationGate,
    slimIntakeSchema,
    slimResultSchema,
    slimReviewSchema,
    snapshotWorktree,
    validateEvidence,
    validateImplementation,
    validateReview,
    validateTask,
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

test("reviewer feedback loops back to builder up to 3 times and succeeds when 3rd fix passes review", async () => {
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
        makeFailedReview(3),
        implementation(),
        review(),
    ]);
    const packet = await runCodeFeature(ctx);
    assert.equal(packet.recommendation, "GO to PR Review");
    assert.equal(packet.fixIterations, 3);
    assert.equal(packet.reviews.length, 4);
    assert.equal(ctx.calls.length, 9);
    assert.equal(ctx.calls[3].options.label, "Code revision (fix 1)");
    assert.match(ctx.calls[3].prompt, /Round 1 concurrency issue found/);
    assert.equal(ctx.calls[5].options.label, "Code revision (fix 2)");
    assert.match(ctx.calls[5].prompt, /Round 2 concurrency issue found/);
    assert.equal(ctx.calls[7].options.label, "Code revision (fix 3)");
    assert.match(ctx.calls[7].prompt, /Round 3 concurrency issue found/);
    assert.equal(packet.pullRequests.length, 1);
});

test("reaching max 3 fix iterations with unresolved reviewer findings stops with NO-GO and creates no PRs", async () => {
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
        implementation(),
        failedReview(),
    ]);
    const packet = await runCodeFeature(ctx);
    assert.equal(packet.recommendation, "NO-GO");
    assert.equal(packet.fixIterations, MAX_FIX_ITERATIONS);
    assert.equal(packet.reviews.length, 4);
    assert.equal(packet.pullRequests.length, 0);
    assert.equal(ctx.calls.length, 9);
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
                return `https://${repoArg.includes("msft.ghe.com") ? repoArg : `github.com/${repoArg}`}/pull/999`;
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
        assert.equal(packet.recommendation, "GO to PR Review");
        assert.equal(packet.pullRequests.length, 2);
        assert.equal(packet.pullRequests[0].status, "created");
        assert.equal(packet.pullRequests[0].url, "https://github.com/AzureAD/microsoft-authentication-library-common-for-android/pull/999");
        assert.equal(packet.pullRequests[1].status, "created");
        assert.equal(packet.pullRequests[1].url, "https://msft.ghe.com/security/ad-accounts-for-android/pull/999");
        assert.ok(existsSync(path.join(commonDir, "common4j/src/main/kotlin/com/microsoft/identity/common/java/throttling/RetryAfterThrottleCache.kt")));
        assert.ok(execCalls.some((c) => c.cmd === "powershell" && c.args.includes("./gradlew :broker4j:test")));
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
                if (cmd === "powershell") throw new Error("Gradle test failed");
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

    const tempRoot = mkdtempSync(path.join(os.tmpdir(), "code-feature-gate-"));
    try {
        const commonDir = path.join(tempRoot, "common");
        mkdirSync(commonDir, { recursive: true });
        writeFileSync(path.join(commonDir, "gradlew.bat"), "@echo off\n", "utf8");

        // 1. Missing local VSTS credentials in settings.gradle -> deferred to CI, not a failure
        const envImpl = implementation();
        const envGate = runLocalVerificationGate({
            repoRoot: commonDir,
            target: {
                id: "common-throttling",
                verificationCommands: ["From common: .\\gradlew.bat :common4j:test"],
            },
            targetImpl: envImpl.targets[0],
            implementation: envImpl,
            exec: (cmd) => {
                if (cmd === "az") return "";
                throw new Error("Could not get unknown property 'project' for Credentials in settings.gradle:13");
            },
        });
        assert.deepEqual(envGate.errors, []);
        assert.equal(envGate.deferredNotes.length, 1);

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
                if (cmd === "az") return "fake-vsts-token";
                if (cmd === "powershell") {
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
                    "?? broker4j/src/main/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottle.kt",
                    "?? broker4j/src/test/com/microsoft/identity/broker4j/broker/throttling/SilentRequestThrottleTest.kt",
                ].join("\n");
            }
            if (args.includes("rev-parse")) return "e428039b7";
            return "";
        };
        const discovered = discoverChangedFilesFromGit(brokerDir, fakeGitExec);
        assert.equal(discovered.files.length, 2);
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

    const observeRepair = {
        verdict: "repair",
        repairKind: "ci-failure",
        summary: "CI :broker4j:compileKotlin failed on SilentRequestIdentity.kt:51.",
        evidence: ["https://msft.ghe.com/security/ad-accounts-for-android/pull/362/checks"],
        actionableFindings: ["Declare explicit isShouldResolveInterrupt() Java getter in BrokerSilentTokenCommandParametersForResourceAccount."],
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
            baseRefName: "dev",
        }),
        exec: (cmd, args) => {
            gitCommands.push({ cmd, args });
            if (cmd === "gh" && args[0] === "auth" && args[1] === "token") return "ghe-token";
            return "";
        },
    });
    assert.equal(result.status, "ready-for-human-merge");
    assert.equal(result.iterations, 2);
    assert.equal(result.humanMergeOnly, true);
    assert.ok(gitCommands.some((c) => c.cmd === "git" && c.args.includes("push")));
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
});




