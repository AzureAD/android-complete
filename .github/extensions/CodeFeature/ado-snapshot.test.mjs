// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
    buildAdoSnapshotScript, ciEvidenceFromSnapshot, dependencyBlockersFromSnapshot,
    parsePullRequestUrl, refreshStaleAdoBuildPolicies, runPrObserver, scopeAdoSnapshot,
} from "./observer.mjs";

const current = "a".repeat(40);
const previous = "b".repeat(40);
const merge = "c".repeat(40);
const prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
const parsed = parsePullRequestUrl(prUrl);
const build = (id, head, status = "inProgress", result = null) => ({
    id, definition: { id: 471091, name: "Android Authenticator PR Checks" },
    status, result, sourceVersion: head === current ? merge : "d".repeat(40),
    parameters: JSON.stringify({ "system.pullRequest.sourceCommitId": head }),
});
const policy = (id, status = "running") => ({
    evaluationId: "01234567-89ab-cdef-0123-456789abcdef", status,
    configuration: { type: { displayName: "Build" }, isBlocking: true },
    context: { buildId: id, buildIsNotCurrent: false },
});
function fixture(overrides = {}) {
    return {
        pr: {
            pullRequestId: 42, title: "Fixture", description: "Human description", status: "active", mergeStatus: "succeeded",
            lastMergeSourceCommit: { commitId: current }, lastMergeCommit: { commitId: merge },
            sourceRefName: "refs/heads/feature", targetRefName: "refs/heads/working",
            repository: { project: { id: "fixture-project" } },
        },
        builds: [build(189679651, current)], extraBuilds: [], policies: [policy(189679651)],
        statuses: [], timeline: { records: [] }, log: [], tests: [], ...overrides,
    };
}
function executeSnapshot(data) {
    const root = mkdtempSync(path.join(os.tmpdir(), "ado-current-head-"));
    try {
        const encoded = Buffer.from(JSON.stringify(data)).toString("base64");
        const script = [
            "$global:LASTEXITCODE = 0",
            `$fixture = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}')) | ConvertFrom-Json`,
            "$global:prReads = 0",
            "function az { 'fixture-token' }",
            "function Invoke-RestMethod {",
            "param($Uri,$Headers,$Method='Get',$Body)",
            "if ($Method -ne 'Get') { throw 'Snapshot attempted an unexpected remote write' }",
            "$requestPath = ([uri]$Uri).AbsolutePath",
            "if ($requestPath -match '/pullRequests/42/iterations/3/changes$') { return @{changeEntries=@(@{item=@{path='/PhoneFactor/FooTest.kt'}})} }",
            "if ($requestPath -match '/pullRequests/42/iterations$') { return @{value=@([pscustomobject]@{id=3})} }",
            "if ($requestPath -match '/pullRequests/42/threads$') { return @{value=@()} }",
            "if ($requestPath -match '/pullRequests/42/statuses$') { return @{value=@($fixture.statuses)} }",
            "if ($requestPath -match '/pullRequests/42$') { $global:prReads++; if ($fixture.changeHead -and $global:prReads -gt 1) { $fixture.pr.lastMergeSourceCommit.commitId = 'changed-head' }; return $fixture.pr }",
            "if ($requestPath -match '/policy/evaluations$') { return @{value=@($fixture.policies)} }",
            "if ($requestPath -match '/build/builds/\\d+/timeline$') { if ($fixture.failTimeline) { throw 'Fixture timeline access denied' }; return $fixture.timeline }",
            "if ($requestPath -match '/build/builds/\\d+/logs/\\d+$') { if ($fixture.failLog) { throw 'Fixture log access denied' }; return @{value=@($fixture.log)} }",
            "if ($requestPath -match '/build/builds/(\\d+)$') { $id=$Matches[1]; return @($fixture.builds + $fixture.extraBuilds | Where-Object { $_.id -eq $id })[0] }",
            "if ($requestPath -match '/build/builds$') { return @{value=@($fixture.builds)} }",
            "if ($requestPath -match '/test/Runs/\\d+/results$') { return @{value=@($fixture.tests)} }",
            "throw \"Unexpected fixture request: $requestPath\"",
            "}",
            buildAdoSnapshotScript(parsed),
        ].join("\n");
        const file = path.join(root, "fixture.ps1");
        writeFileSync(file, script);
        const output = execFileSync("powershell", ["-NoProfile", "-File", file], {
            encoding: "utf8", stdio: "pipe", timeout: 30_000,
        });
        return scopeAdoSnapshot(JSON.parse(output));
    } finally { rmSync(root, { recursive: true, force: true }); }
}
const windowsOnly = { skip: process.platform !== "win32" && "Production PowerShell snapshot fixture" };

test("the actual stale canceled-build/advanced-head scenario stays pending instead of failing the new source", windowsOnly, async () => {
    const snapshot = executeSnapshot(fixture({
        builds: [build(189676684, previous, "cancelling"), build(189679651, current)],
        policies: [policy(189676684, "rejected")],
        statuses: [{ id: 1, iterationId: 3, state: "failed", context: { genre: "Checks", name: "codecoverage" } }],
    }));
    assert.deepEqual(snapshot.prBuilds.map((build) => build.id), [189679651]);
    assert.equal(snapshot.policyEvaluations[0].buildIsNotCurrent, true);
    assert.equal(snapshot.policyEvaluations[0].buildSourceHeadSha, previous);
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
    const calls = [];
    assert.deepEqual(refreshStaleAdoBuildPolicies(parsed, snapshot, { exec: (...args) => calls.push(args) }), []);
    assert.equal(calls.length, 0, "Do not requeue an older policy while the current definition is running");
    const result = await runPrObserver({
        args: { prUrl, maxIterations: 0, options: { maxCiPollAttempts: 0, publishStatus: false } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("No source-repair agent should run for superseded validation"); },
    }, { exec: () => "", fetchPrSnapshot: () => snapshot });
    assert.equal(result.status, "waiting-for-ci");
    assert.equal(result.ci.status, "pending");
    assert.equal(result.repairAttempts, 0);
    assert.equal(result.continuation.args.maxIterations, 0);
});

test("a current policy build absent from the latest-build list is fetched and bound by actual source parameters", windowsOnly, () => {
    const snapshot = executeSnapshot(fixture({ builds: [build(189676684, previous, "completed", "failed")],
        extraBuilds: [build(189679651, current)], policies: [policy(189679651)] }));
    assert.deepEqual(snapshot.prBuilds.map((build) => build.id), [189679651]);
    assert.equal(snapshot.prBuilds[0].sourceHeadSha, current);
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
});

test("a conflicting source parameter overrides an apparently matching merge SHA", windowsOnly, () => {
    const wrong = build(1, previous, "completed", "failed");
    wrong.sourceVersion = merge;
    const snapshot = executeSnapshot(fixture({ builds: [wrong], policies: [policy(1, "rejected")] }));
    assert.deepEqual(snapshot.prBuilds, []);
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
});

test("malformed source parameters are unknown evidence, not silently trusted via a merge fallback", windowsOnly, () => {
    const invalid = { ...build(1, current, "completed", "failed"), parameters: "{malformed" };
    const snapshot = executeSnapshot(fixture({ builds: [invalid], policies: [policy(1, "rejected")] }));
    assert.deepEqual(snapshot.prBuilds, []);
    assert.ok(snapshot.diagnosticErrors.some((line) => line.includes("invalid PR parameters")));
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
});

test("internal Maven/KSP configuration errors are retained and cannot consume source repair slots", windowsOnly, async () => {
    const snapshot = executeSnapshot(fixture({
        builds: [build(1, current, "completed", "failed")], policies: [policy(1, "rejected")],
        timeline: { records: [{ type: "Task", name: "Gradle verification", result: "failed",
            log: { url: "https://dev.azure.com/msazure/One/_apis/build/builds/1/logs/123" } }] },
        log: [
            "BUILD FAILED in 23s", "Plugin [id: 'com.google.devtools.ksp', version: '2.0.21-1.0.27'] was not found",
            "Could not resolve plugin artifact from https://msazure.pkgs.visualstudio.com/One/_packaging/AuthApp/maven/v1",
        ],
    }));
    assert.ok(snapshot.prBuilds[0].failedTasks[0].excerpt.some((line) => line.includes("com.google.devtools.ksp")));
    assert.ok(dependencyBlockersFromSnapshot(snapshot).length > 0);
    const result = await runPrObserver({
        args: { prUrl, options: { maxCiPollAttempts: 0, publishStatus: false } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("A feed configuration failure must not trigger source repairs"); },
    }, { exec: () => "", fetchPrSnapshot: () => snapshot });
    assert.equal(result.blockerKind, "ci-dependencies");
    assert.equal(result.repairAttempts, 0);
    assert.equal(result.ci.status, "failed");
});

test("feed failures discovered by a poll use the same zero-repair dependency handoff", windowsOnly, async () => {
    const failed = executeSnapshot(fixture({
        builds: [build(1, current, "completed", "failed")], policies: [policy(1, "rejected")],
        timeline: { records: [{ type: "Task", name: "Gradle", result: "failed",
            log: { url: "https://dev.azure.com/msazure/One/_apis/build/builds/1/logs/123" } }] },
        log: ["Plugin com.google.devtools.ksp was not found", "Could not resolve plugin artifact from https://msazure.pkgs.visualstudio.com/One/_packaging/AuthApp/maven/v1"],
    }));
    const pending = { ...failed, prBuilds: [{ id: 1, definition: "Checks", sourceHeadSha: current, status: "inProgress", result: null }],
        policyEvaluations: [{ buildId: 1, buildSourceHeadSha: current, blocking: true, status: "running" }] };
    for (const budget of [0, 2]) {
        let reads = 0;
        const result = await runPrObserver({
            args: { prUrl, maxIterations: budget, options: { maxCiPollAttempts: 1, publishStatus: false } },
            step: async (_key, producer) => producer(),
            agent: async () => { throw new Error("A feed-only result from polling must not start a source repair"); },
        }, { exec: () => "", sleep: async () => {}, fetchPrSnapshot: () => ++reads === 1 ? pending : failed });
        assert.equal(result.blockerKind, "ci-dependencies");
        assert.equal(result.repairAttempts, 0);
    }
});

test("real compiler/test defects retain precedence over simultaneous feed failures", windowsOnly, () => {
    const snapshot = executeSnapshot(fixture({
        builds: [build(1, current, "completed", "failed")], policies: [policy(1, "rejected")],
        timeline: { records: [{ type: "Task", name: "Unit tests", result: "failed",
            log: { url: "https://dev.azure.com/msazure/One/_apis/build/builds/1/logs/123" } }] },
        log: ["Unresolved reference: launchForResult", "BUILD FAILED", "Test run id: 12345",
            "Could not resolve plugin from https://msazure.pkgs.visualstudio.com/One/_packaging/AuthApp/maven/v1"],
        tests: [{ automatedTestName: "tenant test", errorMessage: "MockK no answer", stackTrace: "Fixture trace" }],
    }));
    assert.equal(dependencyBlockersFromSnapshot(snapshot).length, 0);
    assert.equal(snapshot.prBuilds[0].failedTasks[0].failedTests[0].testName, "tenant test");
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "failed");
});

test("missing timeline diagnostics stay explicit instead of becoming unexplained source-repair assignments", windowsOnly, async () => {
    const snapshot = executeSnapshot(fixture({
        builds: [build(1, current, "completed", "failed")], policies: [policy(1, "rejected")], failTimeline: true,
    }));
    assert.match(snapshot.prBuilds[0].diagnosticErrors[0], /timeline access denied/);
    const result = await runPrObserver({
        args: { prUrl, options: { maxCiPollAttempts: 0, publishStatus: false } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("No source repair without a diagnostic"); },
    }, { exec: () => "", fetchPrSnapshot: () => snapshot });
    assert.equal(result.blockerKind, "ci-diagnostics");
    assert.ok(result.evidence.some((line) => line.includes("timeline access denied")));
});

test("a failed task name without readable log or issue evidence cannot authorize a source repair", windowsOnly, async () => {
    const snapshot = executeSnapshot(fixture({
        builds: [build(1, current, "completed", "failed")], policies: [policy(1, "rejected")], failLog: true,
        timeline: { records: [{ type: "Task", name: "Generic Gradle task", result: "failed",
            log: { url: "https://dev.azure.com/msazure/One/_apis/build/builds/1/logs/123" } }] },
    }));
    assert.equal(snapshot.prBuilds[0].failedTasks.length, 1);
    assert.match(snapshot.prBuilds[0].diagnosticErrors[0], /log access denied/);
    const result = await runPrObserver({
        args: { prUrl, options: { maxCiPollAttempts: 0, publishStatus: false } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("Task identity alone is not concrete failure diagnostics"); },
    }, { exec: () => "", fetchPrSnapshot: () => snapshot });
    assert.equal(result.blockerKind, "ci-diagnostics");
    assert.equal(result.repairAttempts, 0);
    assert.ok(result.evidence.some((line) => line.includes("log access denied")));
});

test("a source/merge change during collection forces a fresh snapshot, not an old-head pass or failure", windowsOnly, () => {
    const snapshot = executeSnapshot(fixture({ builds: [build(1, current, "completed", "succeeded")],
        policies: [policy(1, "approved")], changeHead: true }));
    assert.equal(snapshot.snapshotHeadChanged, true);
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
});
