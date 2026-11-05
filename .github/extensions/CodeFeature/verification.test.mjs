// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";
import {
    collectBuildReceiptOnDisk, formatPrBody, normalizeImplementationResponse, runCodeFeature,
    BUILDER_AGENT, EVIDENCE_AGENT, REVIEWER_AGENT, rubric,
} from "./workflow.mjs";
import {
    ciEvidenceFromSnapshot, parsePullRequestUrl, publishObserverStatus, refreshStaleAdoBuildPolicies, runPrObserver,
} from "./observer.mjs";
import { deliveryOutcome, pendingVerification, verificationFromGate } from "./verification.mjs";
import { parseMarkerBlock, verificationMarkers, statusMarkers } from "./status.mjs";

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
