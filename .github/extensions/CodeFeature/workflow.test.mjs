// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
    BUILDER_AGENT,
    EVIDENCE_AGENT,
    MAX_FIX_ITERATIONS,
    REVIEWER_AGENT,
    customAgents,
    ensureIsolatedTargetWorktree,
    formatPrBody,
    formatPrTitle,
    isSafeRelativeFilePath,
    normalizeTask,
    resolveRoleSkillGuides,
    rubric,
    runCodeFeature,
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
    const asked = [];
    const interactiveCtx = context([ambiguousEvidence(), implementation(), review()]);
    interactiveCtx.args = { prompt: "Add throttling to Common (AB#3802484)" };
    const completedPacket = await runCodeFeature(interactiveCtx, {
        askUser: async (question) => {
            asked.push(question);
            return "Only silent token requests.";
        },
    });
    assert.equal(completedPacket.recommendation, "GO to PR Review");
    assert.equal(asked.length, 1);
    assert.deepEqual(completedPacket.task.sourceInputs.clarifications, [
        {
            question: "Should throttling apply to interactive token requests or only silent requests?",
            answer: "Only silent token requests.",
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
