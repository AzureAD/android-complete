// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import {
    buildAdoPullRequestPublicationScript, collectBuildReceiptOnDisk, ensureIsolatedTargetWorktree, formatPrBody, isBuilderShortCircuitedToCi, normalizeImplementationResponse, runCodeFeature, validateImplementation,
    BUILDER_AGENT, EVIDENCE_AGENT, REVIEWER_AGENT, rubric,
} from "./workflow.mjs";
import {
    ciEvidenceFromSnapshot, parsePullRequestUrl, publishObserverStatus, refreshStaleAdoBuildPolicies, runPrObserver,
} from "./observer.mjs";
import { deliveryOutcome, pendingVerification, verificationFromGate } from "./verification.mjs";
import { fitAdoDescription, parseMarkerBlock, renderCurrentStatusBlock, renderVerificationBlock, deliveryDecisionMarkers, verificationMarkers, statusMarkers } from "./status.mjs";

const task = {
    title: "Test a verified delivery", revision: "v1", workItemId: 123,
    approval: { approvedRevision: "v1", approvedBy: "owner", reference: "approval" },
    requirements: [{ id: "R1", text: "Preserve correct behavior." }], blockingUnknowns: [],
    targets: [{
        id: "common", repository: "common", localPath: "common", baseBranch: "dev", workItemId: 123,
        title: "Test a verified delivery", description: "Test implementation", acceptanceCriteria: ["Tests pass"],
        verificationCommands: [],
    }],
};
const prUrl = "https://github.com/AzureAD/microsoft-authentication-library-common-for-android/pull/42";
const snapshot = (overrides = {}) => ({
    state: "OPEN", headRefOid: "a".repeat(40), headRefName: "feature", baseRefName: "dev",
    mergeStateStatus: "CLEAN", body: "PR description", statusCheckRollup: [{ name: "CI", state: "SUCCESS" }],
    ...overrides,
});
function ctx(results = [], options = {}) {
    return {
        args: { prUrl, options: { maxCiPollAttempts: 0, publishStatus: false, ...options } },
        calls: [],
        log() {}, phase() {},
        async step(_key, producer) { return producer(); },
        async agent(prompt, opts) {
            this.calls.push({ prompt, opts });
            assert.ok(results.length, `Unexpected agent call: ${opts.label}`);
            return results.shift();
        },
    };
}
const passedReview = {
    verdict: "pass", summary: "Source reviewed.", evidence: ["FooTest.kt"], findings: [],
};
const implementation = () => ({
    taskRevision: "v1",
    targets: [{
        targetId: "common", repository: "common", summary: "Implemented and authored tests.",
        files: [{ path: "FooTest.kt", action: "create", content: "// Copyright Microsoft\nclass FooTest\n", summary: "Tests" }],
        testsAddedOrUpdated: ["FooTest"], verificationCommandsRun: ["claimed command"],
        pr: { title: "Test delivery", summary: "Test change", testPlan: "Local Gradle timed out; tests deferred to CI.", breakingChanges: "None", rolloutAndFlag: "Default-off" },
    }],
    traceability: [{ requirementId: "R1", targetIds: ["common"], filePaths: ["FooTest.kt"], testLocations: ["FooTest"], verification: "Source reviewed only" }],
    assumptions: [], openDecisions: [], blockingUnknowns: [],
});

test("builder timeout, failure, and planned commands never become successful verification receipts", () => {
    for (const summary of [
        "Gradle failed; 5 unit tests are authored. Deferred to CI.",
        "./gradlew test timed out after 12 minutes.",
        "Plan: run ./gradlew test. 5 tests pending.",
    ]) {
        const result = normalizeImplementationResponse({ verdict: "pass", summary, evidence: ["FooTest.kt"] }, task);
        assert.deepEqual(result.targets[0].verificationCommandsRun, []);
        assert.equal(result.targets[0].verification.local.status, "unverified");
        const body = formatPrBody(task.targets[0], { ...implementation().targets[0], ...result.targets[0], pr: implementation().targets[0].pr }, implementation().traceability, 1, 123);
        assert.ok(!body.includes("test target(s) verified"));
        assert.ok(!body.includes("- [x]"));
        assert.match(body, /runtime acceptance is not independently attested/);
        assert.equal(parseMarkerBlock(body, verificationMarkers).local.status, "unverified");
    }
});

test("ADO creation respects the exact UTF-16 description limit and preserves required metadata", () => {
    assert.doesNotThrow(() => buildAdoPullRequestPublicationScript("https://fixture.invalid", { description: "x".repeat(4000) }));
    assert.throws(() => buildAdoPullRequestPublicationScript("https://fixture.invalid", { description: "x".repeat(4001) }), /4000-character limit/);
    const target = { ...task.targets[0], repository: "authenticator" };
    for (const summary of ["Details ".repeat(800), "\uD83D\uDD12".repeat(3000)]) {
        const impl = implementation().targets[0];
        impl.pr.summary = summary;
        impl.pr.testPlan = "Execution deferred to remote CI. ".repeat(100);
        impl.pr.rolloutAndFlag = "Default-off flight with compatibility details. ".repeat(100);
        impl.files = Array.from({ length: 20 }, (_, i) => ({
            path: `${"long_file_name_".repeat(15)}${i}Test.kt`, action: "create", summary: "Authored source",
        }));
        impl.testsAddedOrUpdated = impl.files.map((file) => file.path);
        impl.verification = pendingVerification();
        const body = formatPrBody(target, impl, [], 1, 123);
        assert.ok(body.length <= 3500, `Actual UTF-16 length: ${body.length}`);
        assert.match(body, /WiClose:/);
        assert.match(body, /BEGIN pr-telemetry/);
        assert.match(body, /Additional narrative omitted/);
        assert.equal(parseMarkerBlock(body, statusMarkers).status, "published-for-observation");
        assert.equal(parseMarkerBlock(body, verificationMarkers).local.status, "unverified");
        assert.ok(parseMarkerBlock(body, deliveryDecisionMarkers).rollout.justification.includes("truncated"));
    }
});

test("ADO status compaction does not silently discard human-maintained narrative", () => {
    const humanBody = "Human-maintained details ".repeat(200);
    assert.throws(() => fitAdoDescription(humanBody), /refusing to discard human-maintained/);
    assert.equal(fitAdoDescription("Human-maintained details"), "Human-maintained details");
});

test("a local pass for a previous source head is not promoted to the repaired current head", () => {
    const url = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
    const oldHead = "b".repeat(40);
    const body = renderVerificationBlock({ local: { status: "passed", headSha: oldHead } });
    const updated = publishObserverStatus(parsePullRequestUrl(url), snapshot({ description: body }), "status", { exec: () => "" });
    const verification = parseMarkerBlock(updated, verificationMarkers);
    assert.equal(verification.local.status, "unverified");
    assert.equal(verification.local.headSha, oldHead);
    assert.equal(verification.ci.headSha, "a".repeat(40));
});

test("bounded ADO creation leaves space for final status, current-head CI and actual PR URL", () => {
    const target = { ...task.targets[0], repository: "authenticator" };
    const impl = implementation().targets[0];
    impl.pr.summary = "Implementation details ".repeat(100);
    impl.pr.testPlan = "Builder plan, no receipt. ".repeat(100);
    const body = formatPrBody(target, impl, [], 1, 123);
    const url = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/17820436";
    const block = renderCurrentStatusBlock({
        status: "needs-owner-decision", runId: "12345678-1234-1234-1234-123456789abc", repository: "authenticator",
        prUrl: url, summary: "Concrete diagnostic ".repeat(80), nextAction: "Detailed next action ".repeat(30),
    });
    const updated = publishObserverStatus(parsePullRequestUrl(url), snapshot({
        description: body, provider: "ado", prBuilds: [{ id: 1, status: "completed", result: "succeeded" }],
    }), block, { exec: () => "" });
    assert.ok(updated.length <= 4000, `Final status length: ${updated.length}`);
    assert.equal(parseMarkerBlock(updated, verificationMarkers).ci.status, "passed");
    assert.equal(parseMarkerBlock(updated, statusMarkers).prUrl, url);
});

test("same-session recovery hot-loads changed status, verification, workflow, and observer dependencies", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "workflow-hot-recovery-"));
    try {
        for (const name of ["workflow.mjs", "observer.mjs", "status.mjs", "verification.mjs"]) {
            copyFileSync(new URL(name, import.meta.url), path.join(root, name));
        }
        const workflowUrl = pathToFileURL(path.join(root, "workflow.mjs"));
        const observerUrl = pathToFileURL(path.join(root, "observer.mjs"));
        await import(workflowUrl.href);
        await import(observerUrl.href);
        const replaceSource = (name, transform) => {
            const file = path.join(root, name);
            writeFileSync(file, transform(readFileSync(file, "utf8")));
            const timestamp = new Date(Date.now() + 2000);
            utimesSync(file, timestamp, timestamp);
            return timestamp.getTime();
        };
        replaceSource("status.mjs", (source) => `${source}\nexport const newRecoveryExport = "fresh-status";\n`);
        replaceSource("verification.mjs", (source) => source.replace("No successful verification receipt is available.", "fresh-verification"));
        const workflowVersion = replaceSource("workflow.mjs", (source) =>
            source.replace("    fitAdoDescription,", "    fitAdoDescription, newRecoveryExport,") +
            '\nexport const recoveryProbe = { status: newRecoveryExport, verification: pendingVerification().local.evidence[0] };\n');
        const observerVersion = replaceSource("observer.mjs", (source) =>
            source.replace("    BUILDER_AGENT,", "    BUILDER_AGENT, recoveryProbe,") +
            "\nexport const observerRecoveryProbe = recoveryProbe;\n");
        const freshWorkflow = await import(`${workflowUrl.href}?v=${workflowVersion}`);
        const freshObserver = await import(`${observerUrl.href}?v=${observerVersion}`);
        assert.deepEqual(freshWorkflow.recoveryProbe, { status: "fresh-status", verification: "fresh-verification" });
        assert.deepEqual(freshObserver.observerRecoveryProbe, freshWorkflow.recoveryProbe);
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("the actual corrected-Detekt remote-CI handoff is recognized without claiming a local pass", () => {
    const raw = {
        verdict: "pass",
        summary: "Source implementation handoff, not tests passed.",
        testPlan: "Actual result: exit 1; BUILD FAILED in 9m 49s. Analysis failed with 1 weighted issues. Corrected the source expression; no local Gradle retry was performed. Local verification was short-circuited to remote CI after that correction. Targeted compilation/tests/corrected Detekt remain unverified.",
        evidence: [],
    };
    assert.equal(isBuilderShortCircuitedToCi(raw), true);
    assert.equal(isBuilderShortCircuitedToCi({
        ...raw, testPlan: "Analysis failed with 1 weighted issues. Unresolved reference: Foo. Deferred to remote CI.",
    }), false);
});

test("explicit builder handoff to remote CI does not cause a duplicate deterministic local attempt", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "no-duplicate-gradle-"));
    try {
        const repo = path.join(root, "common");
        mkdirSync(path.join(repo, ".git"), { recursive: true });
        for (const [file, content] of [["Foo.kt", "class Foo\n"], ["FooTest.kt", "class FooTest\n"], ["gradlew.bat", "@echo off\n"]]) {
            writeFileSync(path.join(repo, file), content);
        }
        const scopedTask = structuredClone(task);
        scopedTask.targets[0].verificationCommands = ["./gradlew :common4j:test"];
        const results = [
            { approvalVerification: { verified: true, revision: "v1", reference: "approval", rationale: "Approved" },
                references: [{ id: "E1", repository: "common", revision: "commit", path: "Foo.kt", locator: "Foo" }],
                facts: [{ statement: "Exists", referenceIds: ["E1"] }], conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [] },
            { verdict: "pass", summary: "Authored source and tests; local verification was short-circuited to remote CI.",
                evidence: ["Foo.kt", "FooTest.kt"], testPlan: "Single Gradle attempt failed. Corrected source; no local retry. Verification deferred to remote CI.",
                prTitle: "Fix Foo", prSummary: "Source fix", rolloutAndFlag: "Default-off" },
            passedReview,
        ];
        const context = ctx(results);
        context.args = { task: scopedTask, options: { workspaceRoot: root, applyToWorkspace: true, slimSchemas: true } };
        let gradleCalls = 0;
        const result = await runCodeFeature(context, {
            exec: (command, args) => {
                if (/gradlew/i.test(command)) gradleCalls++;
                if (args.includes("--porcelain")) return " M Foo.kt\n?? FooTest.kt";
                return "";
            },
        });
        assert.equal(gradleCalls, 0);
        assert.equal(result.verification[0].local.status, "deferred");
        assert.ok(!result.reasons.some((reason) => /unit tests.*passed/i.test(reason)));
    } finally { rmSync(root, { recursive: true, force: true }); }
});

async function assertCommittedPublicationReplay({ fixRound = false, tamper = false, verifyLocally = false } = {}) {
    const root = mkdtempSync(path.join(os.tmpdir(), "committed-publication-replay-"));
    const repo = path.join(root, "common");
    try {
        mkdirSync(repo);
        const git = (...args) => execFileSync("git", args, { cwd: repo, encoding: "utf8", stdio: "pipe" }).trim();
        git("init", "--quiet");
        git("config", "user.name", "Workflow Fixture");
        git("config", "user.email", "fixture@example.invalid");
        writeFileSync(path.join(repo, "README"), "baseline\n");
        git("add", "README");
        git("commit", "--quiet", "-m", "baseline");
        git("update-ref", "refs/remotes/origin/dev", "HEAD");
        git("remote", "add", "origin", "https://github.com/AzureAD/microsoft-authentication-library-common-for-android.git");
        for (const [file, text] of [["Foo.kt", "// Copyright Microsoft\nclass Foo\n"], ["FooTest.kt", "// Copyright Microsoft\nclass FooTest\n"]]) {
            writeFileSync(path.join(repo, file), text);
        }
        const fixtureTask = structuredClone(task);
        if (verifyLocally) fixtureTask.targets[0].verificationCommands = ["./gradlew :common4j:test"];
        const rawBuild = {
            verdict: "pass", summary: verifyLocally ? "Authored source and tests." : "Authored source and tests; verification deferred to remote CI.",
            evidence: ["Foo.kt", "FooTest.kt"], testPlan: verifyLocally ? "Run the configured gate." : "Local verification was short-circuited to remote CI.",
            prTitle: "Fix Foo", prSummary: "Source fix", rolloutAndFlag: "Default-off",
        };
        let publicationFails = true;
        let gradleCalls = 0;
        const expectedGateCalls = verifyLocally ? (fixRound ? 2 : 1) : 0;
        const exec = (command, args, options) => {
            if (command === "gh") {
                if (args[0] === "auth" && args[1] === "status") return "Logged in to github.com account fixture (keyring)";
                if (args[0] === "auth" && args[1] === "token") return "fixture-token";
                if (args[0] === "pr" && args[1] === "create") {
                    if (publicationFails) throw new Error("Fixture publication unavailable after commit");
                    return prUrl;
                }
                return "";
            }
            if (/gradlew/i.test(command)) {
                gradleCalls++;
                if (!verifyLocally) throw new Error("Unexpected duplicate local verification");
                return "BUILD SUCCESSFUL";
            }
            if (command === "git" && (args.includes("fetch") || args.includes("push"))) return "";
            return execFileSync(command, args, { ...options, encoding: "utf8", stdio: "pipe" }).trim();
        };
        const before = normalizeImplementationResponse(rawBuild, fixtureTask, { workspaceRoot: root, exec });
        const journal = new Map();
        const make = (results) => {
            const context = ctx(results);
            const agent = context.agent;
            context.agent = (prompt, options) => {
                if (!results.length) {
                    const data = JSON.parse(prompt.slice(prompt.lastIndexOf("DATA:\n") + 6));
                    throw new Error(`Unexpected ${options.label}: ${JSON.stringify(data.history || [])}`);
                }
                if (options.label === "Code revision (fix 1)") {
                    writeFileSync(path.join(repo, "Foo.kt"), "// Copyright Microsoft\nclass Foo { val fixed = true }\n");
                }
                return agent.call(context, prompt, options);
            };
            context.args = { task: fixtureTask, options: { workspaceRoot: root, createPullRequests: true, slimSchemas: true } };
            context.runId = "fixture-publication-replay";
            context.step = async (key, producer, options) => {
                if (options?.volatile) return producer();
                if (!journal.has(key)) journal.set(key, JSON.parse(JSON.stringify(await producer())));
                return JSON.parse(JSON.stringify(journal.get(key)));
            };
            return context;
        };
        const evidence = {
            approvalVerification: { verified: true, revision: "v1", reference: "approval", rationale: "Approved" },
            references: [{ id: "E1", repository: "common", revision: "commit", path: "Foo.kt", locator: "Foo" }],
            facts: [{ statement: "Exists", referenceIds: ["E1"] }], conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
        };
        const results = fixRound
            ? [evidence, rawBuild, { ...passedReview, verdict: "repair", findings: ["Foo.kt: fix behavior"] }, rawBuild, passedReview]
            : [evidence, rawBuild, passedReview];
        await assert.rejects(runCodeFeature(make(results), { exec }), /Fixture publication unavailable/);
        const codeStatus = () => git("status", "--porcelain").split(/\r?\n/).filter((line) => line && line !== "?? local.properties").join("\n");
        assert.equal(codeStatus(), "");
        const committedHead = git("rev-parse", "HEAD");
        ensureIsolatedTargetWorktree(root, fixtureTask.targets[0], exec);
        assert.equal(git("rev-parse", "HEAD"), committedHead);
        const after = normalizeImplementationResponse(rawBuild, fixtureTask, { workspaceRoot: root, exec });
        assert.deepEqual(after.targets[0].files.map((file) => file.path), before.targets[0].files.map((file) => file.path));
        assert.deepEqual(after.targets[0].testsAddedOrUpdated, before.targets[0].testsAddedOrUpdated);
        assert.deepEqual(validateImplementation(after, fixtureTask, { workspaceRoot: root }), []);
        publicationFails = false;
        const replay = make([]);
        if (tamper) {
            writeFileSync(path.join(repo, "Foo.kt"), "// Human edit after source review\nclass Foo\n");
            await assert.rejects(runCodeFeature(replay, { exec }), /Reviewed implementation changed/);
            assert.equal(replay.calls.length, 0);
            assert.equal(gradleCalls, expectedGateCalls);
            assert.equal(git("rev-parse", "HEAD"), committedHead);
            assert.match(readFileSync(path.join(repo, "Foo.kt"), "utf8"), /Human edit after source review/);
            return;
        }
        const result = await runCodeFeature(replay, { exec });
        assert.equal(result.pullRequests[0].url, prUrl);
        assert.equal(replay.calls.length, 0);
        assert.equal(gradleCalls, expectedGateCalls);
        assert.equal(git("rev-parse", "HEAD"), committedHead);
        assert.equal(codeStatus(), "");
    } finally { rmSync(root, { recursive: true, force: true }); }
}

test("committed slim metadata survives publication-error replay without agents or builds", () => assertCommittedPublicationReplay());
test("publication-error replay after a source-changing fix reuses the final reviewed checkpoint", () => assertCommittedPublicationReplay({ fixRound: true }));
test("publication replay refuses changed reviewed content without overwriting human edits", () => assertCommittedPublicationReplay({ tamper: true }));
test("publication replay after a source-changing fix does not repeat either successful gate", () => assertCommittedPublicationReplay({ fixRound: true, verifyLocally: true }));

test("compile directories alone do not attest successful tests", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "verification-receipt-"));
    try {
        mkdirSync(path.join(root, "common4j", "build", "classes"), { recursive: true });
        assert.equal(collectBuildReceiptOnDisk(root, { exec: () => "" }).verified, false);
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("deterministic gate outcomes distinguish success, partial execution, failed, and deferred", () => {
    const base = { outcomes: { passed: [], failed: [], deferred: [] }, ranCommands: [], errors: [], deferredNotes: [] };
    assert.equal(verificationFromGate(base).local.status, "unverified");
    assert.equal(verificationFromGate({ ...base, outcomes: { passed: [{ command: "test" }] } }).local.status, "passed");
    assert.equal(verificationFromGate({ ...base, outcomes: { passed: [{ command: "compile" }], deferred: [{ command: "test" }] } }).local.status, "deferred");
    assert.equal(verificationFromGate({ ...base, outcomes: { failed: [{ command: "test" }] } }).local.status, "failed");
});

test("delivery status never promotes pending, failed, unknown, exhausted, or unpublished CI to GO", () => {
    const prs = [{ url: prUrl }];
    assert.equal(deliveryOutcome(prs, []), "ci-not-observed");
    assert.equal(deliveryOutcome(prs, [{ status: "waiting-for-ci", ci: { status: "pending" } }]), "waiting-for-ci");
    assert.equal(deliveryOutcome(prs, [{ status: "max-iterations-reached", ci: { status: "failed" } }]), "ci-failed");
    assert.equal(deliveryOutcome(prs, [{ status: "max-iterations-reached", ci: { status: "unknown" } }]), "needs-owner-decision");
    assert.equal(deliveryOutcome(prs, [{ status: "awaiting-human-review", ci: { status: "passed" } }]), "awaiting-human-review");
    assert.equal(deliveryOutcome(prs, [{ status: "ready-for-human-merge", ci: { status: "passed" } }]), "ready-for-human-review");
});

test("CI evidence requires successful current-head checks and retains precise failure diagnostics", () => {
    assert.equal(ciEvidenceFromSnapshot(snapshot()).status, "passed");
    assert.equal(ciEvidenceFromSnapshot(snapshot({ headRefOid: null })).status, "unknown");
    assert.equal(ciEvidenceFromSnapshot(snapshot({ statusCheckRollup: [] })).status, "unknown");
    assert.equal(ciEvidenceFromSnapshot(snapshot({ provider: "ado" })).status, "unknown");
    assert.equal(ciEvidenceFromSnapshot(snapshot({ policyEvaluations: [{ buildId: 1, blocking: true, buildIsNotCurrent: true }] })).status, "pending");
    const failed = ciEvidenceFromSnapshot(snapshot({
        prBuilds: [{ id: 2, status: "completed", result: "failed", failedTasks: [{ name: "Unit tests", failedTests: [{ testName: "tenant test", errorMessage: "MockK no answer" }] }] }],
    }));
    assert.equal(failed.status, "failed");
    assert.ok(failed.evidence.some((line) => line.includes("MockK no answer")));
    assert.equal(ciEvidenceFromSnapshot(snapshot({ prBuilds: [{ status: "completed", result: "partiallySucceeded" }] })).status, "failed");
});

test("exhausted observer returns final CI failures and never exceeds its two-repair ceiling", async () => {
    const repair = { verdict: "pass", summary: "Fixed test", evidence: ["FooTest.kt"] };
    const context = ctx([repair, passedReview, repair, passedReview], { pushRepairs: false });
    let reads = 0;
    const result = await runPrObserver(context, {
        exec: () => "",
        fetchPrSnapshot: () => snapshot({ headRefOid: String(++reads).repeat(40), prBuilds: [{ id: 77, status: "completed", result: "failed", failedTasks: [{ name: "Compile", excerpt: ["FooTest.kt:12 Unresolved reference"] }] }] }),
    });
    assert.equal(result.status, "max-iterations-reached");
    assert.equal(result.iterations, 2);
    assert.equal(context.calls.filter((call) => call.opts.agent === BUILDER_AGENT).length, 2);
    assert.equal(result.ci.status, "failed");
    assert.ok(result.evidence.some((line) => line.includes("Unresolved reference")));
    assert.ok(result.actionableFindings.length);
    assert.equal(result.continuation.workflow, "pr-observer");
});

test("pending CI returns explicit continuation without spending classifier agents", async () => {
    const context = ctx();
    const result = await runPrObserver(context, {
        exec: () => "", fetchPrSnapshot: () => snapshot({ statusCheckRollup: [{ name: "CI", state: "PENDING" }] }),
    });
    assert.equal(result.status, "waiting-for-ci");
    assert.equal(result.ci.status, "pending");
    assert.equal(context.calls.length, 0);
    assert.equal(result.continuation.args.prUrl, prUrl);
});

test("human-only policy gates are explicit and cannot trigger code repairs", async () => {
    const context = ctx();
    const result = await runPrObserver(context, {
        exec: () => "",
        fetchPrSnapshot: () => snapshot({
            mergeStateStatus: "BLOCKED",
            policyEvaluations: [{ name: "Minimum reviewers", blocking: true, status: "queued" }],
        }),
    });
    assert.equal(result.status, "awaiting-human-review");
    assert.equal(result.ci.status, "passed");
    assert.equal(result.remainingPolicies.length, 1);
    assert.equal(context.calls.length, 0);
});

test("successful CI with merge conflicts still reaches classification and tactical repair", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "observer-conflict-"));
    try {
        const context = ctx([
            { verdict: "repair", repairKind: "merge-conflict", summary: "Merge conflict", evidence: ["DIRTY"], actionableFindings: ["Resolve the reported conflict"] },
            { verdict: "blocked", summary: "Fixture conflict repair reached", evidence: [] },
        ], { workspaceRoot: root });
        const result = await runPrObserver(context, {
            exec: () => "", fetchPrSnapshot: () => snapshot({ mergeStateStatus: "DIRTY" }),
        });
        assert.equal(result.status, "needs-owner-decision");
        assert.equal(result.summary, "Fixture conflict repair reached");
        assert.equal(context.calls.filter((call) => call.opts.agent === BUILDER_AGENT).length, 1);
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("hosted runner brownouts retain actual infrastructure evidence without code-repair agents", async () => {
    const context = ctx();
    const result = await runPrObserver(context, {
        exec: () => "",
        fetchPrSnapshot: () => snapshot({
            prBuilds: [{ id: 189612939, status: "completed", result: "canceled",
                infrastructureIssues: ["macOS-14 image brownout: hosted provider unable to process request."] }],
        }),
    });
    assert.equal(result.blockerKind, "ci-infrastructure");
    assert.equal(result.ci.status, "failed");
    assert.ok(result.evidence.some((line) => line.includes("brownout")));
    assert.equal(context.calls.length, 0);
    assert.equal(deliveryOutcome([{ url: prUrl }], [result]), "infrastructure-blocked");
});

test("proven brownout takes precedence over pending coverage without waiting or agents", async () => {
    const context = ctx([], { maxCiPollAttempts: 2 });
    const result = await runPrObserver(context, {
        exec: () => "",
        sleep: async () => { throw new Error("Must not wait on already-proven provider outage"); },
        fetchPrSnapshot: () => snapshot({
            statusCheckRollup: [{ name: "coverage", state: "pending" }],
            prBuilds: [{ id: 1, status: "completed", result: "canceled", infrastructureIssues: ["Scheduled macOS-14 brownout"] }],
        }),
    });
    assert.equal(result.blockerKind, "ci-infrastructure");
    assert.equal(deliveryOutcome([{ url: prUrl }], [result]), "infrastructure-blocked");
    assert.equal(context.calls.length, 0);
});

test("standalone observer applies the approved JDK to repair verification without global mutation", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "observer-jdk-"));
    const originalJavaHome = process.env.JAVA_HOME;
    try {
        const jdk = path.join(root, "jdk21");
        mkdirSync(path.join(jdk, "bin"), { recursive: true });
        writeFileSync(path.join(jdk, "release"), 'JAVA_VERSION="21.0.4"\n');
        for (const tool of ["java", "javac"]) writeFileSync(path.join(jdk, "bin", `${tool}${process.platform === "win32" ? ".exe" : ""}`), "");
        mkdirSync(path.join(root, "common"));
        writeFileSync(path.join(root, "common", "gradlew.bat"), "");
        const context = ctx([{ verdict: "pass", summary: "Source fixed", evidence: [] }, passedReview], {
            workspaceRoot: root, javaHome: jdk, runLocalVerification: true, pushRepairs: false,
        });
        context.args.verificationCommands = ["./gradlew :common4j:test"];
        let reads = 0;
        let gateJavaHome = null;
        const result = await runPrObserver(context, {
            exec: (command, _args, options) => {
                if (/gradlew/i.test(command)) gateJavaHome = options.env.JAVA_HOME;
                return "";
            },
            fetchPrSnapshot: () => ++reads === 1
                ? snapshot({ prBuilds: [{ id: 1, status: "completed", result: "failed", failedTasks: [{ name: "Compile", excerpt: ["Unresolved reference"] }] }] })
                : snapshot(),
        });
        assert.equal(result.ci.status, "passed");
        assert.equal(gateJavaHome, jdk);
        assert.equal(process.env.JAVA_HOME, originalJavaHome);
        await assert.rejects(runPrObserver(ctx([], { javaHome: path.join(root, "missing") }), {}), /toolchain requirements/);
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("stale required ADO build validation is refreshed once per durable observation, without code agents", async () => {
    const parsed = parsePullRequestUrl("https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42");
    const id = "01234567-89ab-cdef-0123-456789abcdef";
    let patches = 0;
    const policies = [
        { evaluationId: id, buildId: 1, blocking: true, buildIsNotCurrent: true, status: "approved" },
        { evaluationId: "do-not-patch", buildId: 2, blocking: false, buildIsNotCurrent: true, status: "approved" },
    ];
    assert.deepEqual(refreshStaleAdoBuildPolicies(parsed, { policyEvaluations: policies }, {
        exec: (_command, args) => { assert.ok(args.at(-1).includes(`${id}?api-version=`)); patches++; return ""; },
    }), [id]);
    assert.equal(patches, 1);
    const journal = new Map();
    const context = ctx([], { publishStatus: false });
    context.args.prUrl = parsed.prUrl;
    context.step = async (key, producer, options) => {
        if (options?.volatile) return producer();
        if (!journal.has(key)) journal.set(key, await producer());
        return journal.get(key);
    };
    let refreshes = 0;
    const dependencies = {
        exec: () => "",
        fetchPrSnapshot: () => snapshot({ provider: "ado", policyEvaluations: structuredClone(policies),
            prBuilds: [{ id: 1, status: "completed", result: "succeeded" }] }),
        refreshStaleBuilds: () => { refreshes++; return [id]; },
    };
    assert.equal((await runPrObserver(context, dependencies)).status, "waiting-for-ci");
    assert.equal((await runPrObserver(context, dependencies)).ci.status, "pending");
    assert.equal(refreshes, 1);
    assert.equal(context.calls.length, 0);
});

test("PR status publication preserves other content, guards concurrent edits, and records write failures", async () => {
    const captured = [];
    const snap = snapshot();
    const resultBody = publishObserverStatus(parsePullRequestUrl(prUrl), snap, "new status", {
        exec: (_command, args) => {
            captured.push(args);
            if (args.includes("--method")) return "{}";
            if (args.includes("token")) return "fixture-token";
            if (args[0] === "auth") return "Logged in to github.com account shahzaibj";
            return JSON.stringify({ state: "open", head: { sha: snap.headRefOid }, body: snap.body });
        },
    });
    assert.ok(resultBody.startsWith(snap.body));
    assert.ok(captured.some((args) => args.includes("PATCH")));
    assert.throws(() => publishObserverStatus(parsePullRequestUrl(prUrl), snap, "new status", {
        exec: (_command, args) => args[0] === "api" ? JSON.stringify({ state: "open", head: { sha: "different" }, body: snap.body }) : "",
    }), /PR changed/);
    const context = ctx([], { publishStatus: true });
    const result = await runPrObserver(context, {
        fetchPrSnapshot: () => snapshot(), publishStatus: () => { throw new Error("Write denied"); }, exec: () => "",
    });
    assert.equal(result.statusPublication.status, "failed");
    assert.ok(result.evidence.some((line) => line.includes("Write denied")));
});

test("ADO final status executes its production script with head and description guards", {
    skip: process.platform !== "win32" && "Windows PowerShell publication fixture",
}, () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "observer-publication-"));
    try {
        const parsed = parsePullRequestUrl("https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42");
        const snap = snapshot({ description: "Human-maintained description.\nOriginal details." });
        for (const changed of [false, true]) {
            let invoked = false;
            const action = () => publishObserverStatus(parsed, snap, "<!-- fixture status -->", {
                workspaceRoot: root,
                exec: (_command, args) => {
                    invoked = true;
                    const source = [
                        "$global:LASTEXITCODE = 0",
                        "function az { 'fixture-token' }",
                        "function Invoke-RestMethod { param($Uri,$Headers,$Method,$Body); if ($Method -eq 'Get') {",
                        `$original = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${Buffer.from(snap.description).toString("base64")}'))`,
                        `return @{status='active';lastMergeSourceCommit=@{commitId='${changed ? "b".repeat(40) : snap.headRefOid}'};description=$original} }`,
                        "$payload = [Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json",
                        "if ($payload.description -notmatch 'Human-maintained description' -or $payload.description -notmatch 'fixture status') { throw 'Description was not preserved' }",
                        "}",
                        args.at(-1),
                    ].join("\n");
                    writeFileSync(path.join(root, "fixture.ps1"), source);
                    return execFileSync("powershell", ["-NoProfile", "-File", path.join(root, "fixture.ps1")], { cwd: root, encoding: "utf8", stdio: "pipe" });
                },
            });
            if (changed) assert.throws(action, /PR changed during observer status publication/);
            else assert.doesNotThrow(action);
            assert.equal(invoked, true);
        }
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("production code-feature packet propagates observer CI failure instead of reporting tests passed", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "delivery-packet-"));
    try {
        mkdirSync(path.join(root, "common"));
        const context = ctx();
        context.args = { task, options: { workspaceRoot: root, createPullRequests: true, observePullRequests: true, maxCiPollAttempts: 0, pushRepairs: false, publishStatus: false } };
        context.agent = async (_prompt, options) => {
            if (options.agent === EVIDENCE_AGENT) return {
                approvalVerification: { verified: true, revision: "v1", reference: "approval", rationale: "Approved" },
                references: [{ id: "E1", repository: "common", revision: "commit", path: "Foo.kt", locator: "Foo" }],
                facts: [{ statement: "Exists", referenceIds: ["E1"] }], conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
            };
            if (options.agent === BUILDER_AGENT) return implementation();
            if (options.agent === REVIEWER_AGENT) return {
                criteria: rubric.map((id) => ({ id, pass: true })),
                acceptanceCriteria: [{ targetId: "common", criterion: "Tests pass", passed: true, evidence: "Source reviewed" }],
                findings: [], blockingUnknowns: [],
            };
            throw new Error("Unexpected classifier agent");
        };
        const result = await runCodeFeature(context, {
            exec: (command, args) => {
                if (command === "gh" && args[0] === "pr" && args[1] === "create") return prUrl;
                if (command === "git" && args[0] === "rev-parse") return "a".repeat(40);
                return "";
            },
            fetchPrSnapshot: () => snapshot({ statusCheckRollup: [{ name: "CI", state: "PENDING" }] }),
        });
        assert.equal(result.recommendation, "waiting-for-ci");
        assert.equal(result.deliveryStatus, "waiting-for-ci");
        assert.equal(result.verification[0].ci.status, "pending");
        assert.ok(!result.reasons.some((reason) => /unit tests.*passed/i.test(reason)));
        assert.equal(parseMarkerBlock(result.pullRequests[0].body, statusMarkers).status, "waiting-for-ci");
        assert.equal(result.observations[0].continuation.workflow, "pr-observer");
    } finally { rmSync(root, { recursive: true, force: true }); }
});
