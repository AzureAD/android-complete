// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
    buildAdoSnapshotScript, ciEvidenceFromSnapshot, dependencyBlockersFromSnapshot,
    parsePullRequestUrl, refreshStaleAdoBuildPolicies, retryFailedAdoBuildStages, runPrObserver, scopeAdoSnapshot,
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
    configuration: { type: { id: "0609b952-1397-4640-95ec-e00a01b2c241", displayName: "Build" }, isBlocking: true },
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
            "if ($requestPath -match '/build/builds/(\\d+)$') { $id=$Matches[1]; if ([long]$id -le 0) { throw 'Fixture rejected nonexistent build zero' }; if ($fixture.failBuildMetadata) { throw 'Fixture build metadata denied' }; return @($fixture.builds + $fixture.extraBuilds | Where-Object { $_.id -eq $id })[0] }",
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

test("invalid policy build IDs never become metadata requests or a passed required build", windowsOnly, () => {
    const invalid = [null, 0, "0", -1, "-1", "", "missing", "1.5", 1.5, false];
    const snapshot = executeSnapshot(fixture({
        builds: [build(1, current, "completed", "succeeded")],
        policies: invalid.map((id) => policy(id, "approved")),
    }));
    assert.deepEqual(snapshot.policyEvaluations.map((item) => item.buildId), invalid.map(() => null));
    assert.deepEqual(snapshot.diagnosticErrors, []);
    assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
});

test("a real positive policy build lookup failure stays explicit", windowsOnly, () => {
    const snapshot = executeSnapshot(fixture({
        builds: [], policies: [policy("123", "running")], failBuildMetadata: true,
    }));
    assert.equal(snapshot.policyEvaluations[0].buildId, 123);
    assert.ok(snapshot.diagnosticErrors.some((line) => /Build 123 metadata unavailable.*denied/.test(line)));
    assert.notEqual(ciEvidenceFromSnapshot(snapshot).status, "passed");
});

test("native checkout diagnostics trigger a bounded remote-only retry without repair agents", windowsOnly, async () => {
    const failed = executeSnapshot(fixture({
        builds: [build(77, current, "completed", "failed")], policies: [policy(77, "rejected")],
        timeline: { records: [{ type: "Task", name: "Checkout", result: "failed",
            log: { url: "https://dev.azure.com/msazure/One/_apis/build/builds/77/logs/123" } }] },
        log: ["curl 56 Connection reset by peer", "fatal: fetch-pack: invalid index-pack output"],
    }));
    assert.ok(failed.prBuilds[0].failedTasks[0].excerpt.some((line) => line.includes("Connection reset")));
    let retries = 0;
    const result = await runPrObserver({
        args: { prUrl, options: { maxCiPollAttempts: 1, ciPollIntervalMs: 0, publishStatus: false } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("Checkout connectivity is not a source repair"); },
    }, {
        exec: () => "", sleep: async () => {}, fetchPrSnapshot: () => failed,
        retryFailedBuilds: () => { retries++; return true; },
    });
    assert.equal(retries, 1);
    assert.equal(result.repairAttempts, 0);
    assert.equal(result.blockerKind, "ci-infrastructure");
});

test("native retry touches failed stages only and requires an actual boolean acknowledgement", windowsOnly, async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "ado-retry-stages-"));
    try {
        const writes = path.join(root, "writes.jsonl");
        const exec = (command, args) => {
            assert.equal(command, "powershell");
            const setup = [
                "$global:LASTEXITCODE = 0",
                "function az { 'fixture-token' }",
                "function Invoke-RestMethod { param($Uri,$Headers,$Method='Get',$Body)",
                "if ($Method -eq 'Get' -and $Uri -match '/timeline\\?') { return @{records=@(" +
                    "@{type='Stage';state='completed';result='succeeded';identifier='successful'}," +
                    "@{type='Stage';state='completed';result='failed';identifier='failed'})} }",
                `if ($Method -eq 'Patch') { Add-Content -LiteralPath '${writes.replace(/'/g, "''")}' -Value $Uri; return @{} }`,
                "throw \"Unexpected retry request: $Uri\"",
                "}",
                args[args.indexOf("-Command") + 1],
            ].join("\n");
            const file = path.join(root, "fixture.ps1");
            writeFileSync(file, setup);
            return execFileSync("powershell", ["-NoProfile", "-File", file], { encoding: "utf8", stdio: "pipe", timeout: 30_000 });
        };
        assert.equal(await retryFailedAdoBuildStages(parsed, { prBuilds: [{ id: 77, result: "failed" }] }, { exec }), true);
        const requests = readFileSync(writes, "utf8").trim().split(/\r?\n/);
        assert.equal(requests.length, 1);
        assert.match(requests[0], /\/stages\/failed\?/);
        assert.doesNotMatch(requests[0], /successful/);
        assert.equal(await retryFailedAdoBuildStages(parsed, { prBuilds: [{ id: 77, result: "failed" }] }, { exec: () => "not true" }), false);
        const abort = Object.assign(new Error("Cancelled retry"), { name: "AbortError" });
        await assert.rejects(() => retryFailedAdoBuildStages(parsed, { prBuilds: [{ id: 77, result: "failed" }] }, {
            exec: () => { throw abort; },
        }), (error) => error === abort);
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("checkout recovery is stage-only when identifiers or stage PATCH acknowledgements are unavailable", windowsOnly, async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "ado-stage-only-"));
    try {
        for (const scenario of ["missing-identifier", "patch-failed", "partial-stage-failure"]) {
            for (const stageOnly of [true, false]) {
                const writes = path.join(root, `${scenario}-${stageOnly}.jsonl`);
                const scriptExec = (command, args) => {
                    assert.equal(command, "powershell");
                    const setup = [
                        "$global:LASTEXITCODE = 0",
                        "function az { 'fixture-token' }",
                        "function Invoke-RestMethod { param($Uri,$Headers,$Method='Get',$Body)",
                        `if ($Method -eq 'Patch') { Add-Content -LiteralPath '${writes.replace(/'/g, "''")}' -Value $Uri; if ($Uri -match '/stages/failed\\?' -and '${scenario}' -ne 'missing-identifier') { throw 'Fixture stage PATCH denied' }; return @{} }`,
                        "if ($Uri -match '/timeline\\?') { return @{records=@(" +
                            "@{type='Stage';state='completed';result='succeeded';identifier='successful'}," +
                            `@{type='Stage';state='completed';result='failed';identifier=${scenario === "missing-identifier" ? "''" : "'failed'"} }` +
                            (scenario === "partial-stage-failure" ? ",@{type='Stage';state='completed';result='failed';identifier='accepted'}" : "") + ")} }",
                        "if ($Uri -match '/pullRequests/42\\?') { return @{repository=@{project=@{id='fixture-project'}}} }",
                        "if ($Uri -match '/policy/evaluations\\?') { return @{value=@(@{evaluationId='fixture';context=@{buildId=77}})} }",
                        "throw \"Unexpected retry request: $Uri\"",
                        "}",
                        args[args.indexOf("-Command") + 1],
                    ].join("\n");
                    const file = path.join(root, "fixture.ps1");
                    writeFileSync(file, setup);
                    return execFileSync("powershell", ["-NoProfile", "-File", file], { encoding: "utf8", stdio: "pipe", timeout: 30_000 });
                };
                const acknowledged = await retryFailedAdoBuildStages(parsed, { prBuilds: [{ id: 77, result: "failed" }] }, { exec: scriptExec, stageOnly });
                assert.equal(acknowledged, !stageOnly);
                const requests = existsSync(writes) ? readFileSync(writes, "utf8").trim().split(/\r?\n/) : [];
                assert.equal(requests.some((uri) => /\/policy\/evaluations\//.test(uri)), !stageOnly && scenario !== "partial-stage-failure");
                assert.equal(requests.some((uri) => /\/stages\/successful\?/.test(uri)), false);
            }
        }
        const observed = executeSnapshot(fixture({
            builds: [build(77, current, "completed", "failed")], policies: [policy(77, "rejected")],
            timeline: { records: [{ type: "Task", name: "Checkout", result: "failed",
                log: { url: "https://dev.azure.com/msazure/One/_apis/build/builds/77/logs/123" } }] },
            log: ["curl 56 Connection reset by peer"],
        }));
        let retries = 0;
        const result = await runPrObserver({
            args: { prUrl, options: { maxCiPollAttempts: 1, publishStatus: false } },
            step: async (_key, producer) => producer(),
            agent: async () => { throw new Error("Unconfirmed stage retry must hand off, not repair source"); },
        }, {
            exec: () => "", fetchPrSnapshot: () => observed,
            retryFailedBuilds: (_pr, _snapshot, options) => {
                retries++;
                assert.equal(options.stageOnly, true);
                return false;
            },
        });
        assert.equal(retries, 1);
        assert.equal(result.blockerKind, "ci-infrastructure");
        assert.match(result.summary, /retry could not be confirmed/);
        assert.equal(result.repairAttempts, 0);
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("required build policies without a queued build ID remain pending despite a different successful build", windowsOnly, async () => {
    for (const status of ["queued", "running", "rejected", "approved"]) {
        const queuedPolicy = policy(null, status);
        queuedPolicy.configuration.type.displayName = "Localized validation";
        const snapshot = executeSnapshot(fixture({
            builds: [build(1, current, "completed", "succeeded")], policies: [queuedPolicy],
        }));
        assert.equal(ciEvidenceFromSnapshot(snapshot).status, "pending");
        const result = await runPrObserver({
            args: { prUrl, maxIterations: 0, options: { maxCiPollAttempts: 0, publishStatus: false } },
            step: async (_key, producer) => producer(),
            agent: async () => { throw new Error("Missing required build results must not trigger source agents"); },
        }, { exec: () => "", fetchPrSnapshot: () => snapshot });
        assert.equal(result.status, "waiting-for-ci");
        assert.equal(result.continuation.args.maxIterations, 0);
        assert.equal(result.ci.status, "pending");
    }
    const human = policy(null, "queued");
    human.configuration.type = { id: "fa4e907d-c16b-4a4c-9dfa-4906e5d171dd", displayName: "Minimum number of reviewers" };
    assert.equal(ciEvidenceFromSnapshot(executeSnapshot(fixture({
        builds: [build(1, current, "completed", "succeeded")], policies: [human],
    }))).status, "passed", "Human approvals remain separate from executable build validation");
});

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
            "Caching disabled for transform 716c840325e2f4e116156161cfb5b22a and file403abc",
            "BUILD FAILED in 23s", "Plugin [id: 'com.google.devtools.ksp', version: '2.0.21-1.0.27'] was not found",
            "Could not resolve plugin artifact from https://msazure.pkgs.visualstudio.com/One/_packaging/AuthApp/maven/v1",
        ],
    }));
    assert.ok(snapshot.prBuilds[0].failedTasks[0].excerpt.some((line) => line.includes("com.google.devtools.ksp")));
    assert.ok(!snapshot.prBuilds[0].failedTasks[0].excerpt.some((line) => line.includes("Caching disabled")));
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

test("read-only observer accepts only the configured target and carries it to the ADO snapshot", () => {
    const github = { provider: "github", hostname: "github.com", slug: "example/project", localPath: ".", baseBranch: "dev" };
    assert.ok(parsePullRequestUrl("https://github.com/example/project/pull/42", github));
    assert.equal(parsePullRequestUrl("https://github.com/AzureAD/android-complete/pull/42", github), null);
    const ado = { provider: "ado", hostname: "dev.azure.com", slug: "example/Engineering/Project", localPath: ".", baseBranch: "dev" };
    const target = parsePullRequestUrl("https://dev.azure.com/example/Engineering/_git/Project/pullrequest/42", ado);
    assert.ok(target);
    assert.doesNotThrow(() => buildAdoSnapshotScript(target, ado));
    assert.equal(parsePullRequestUrl(prUrl, ado), null);
});

test("read-only observation never mutates or delegates and distinguishes CI and review states", async () => {
    const trustedRepository = { provider: "github", hostname: "github.com", slug: "example/project",
        localPath: ".", baseBranch: "dev", headSha: current, headBranch: "feature" };
    const url = "https://github.com/example/project/pull/42";
    const snapshot = {
        provider: "github", number: 42, url, state: "OPEN", baseRefName: "dev",
        headRefName: "feature", headRefOid: current, mergeStateStatus: "CLEAN",
        reviewThreadsComplete: true, unresolvedReviewThreadCount: 0,
        statusCheckRollup: [{ name: "CI", status: "COMPLETED", conclusion: "SUCCESS" }],
    };
    const observe = async (value, extra = {}) => runPrObserver({
        args: { prUrl: url, options: { maxCiPollAttempts: 0, publishStatus: true } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("Read-only observation must not invoke an agent"); },
    }, {
        observeOnly: true, trustedRepository, exec: () => "", fetchPrSnapshot: () => value,
        publishStatus: () => { throw new Error("Read-only observation must not publish status"); },
        refreshStaleBuilds: () => { throw new Error("Read-only observation must not refresh policies"); },
        retryFailedBuilds: () => { throw new Error("Read-only observation must not retry builds"); },
        onRepairAttempt: () => { throw new Error("Read-only observation must not repair"); },
        ...extra,
    });
    const ready = await observe(snapshot);
    assert.equal(ready.status, "ready-for-human-merge");
    assert.equal(ready.ci.status, "passed");
    for (const changed of [
        { reviewThreadsComplete: false },
        { unresolvedReviewThreadCount: 1 },
        { activeReviewThreads: [{ id: 8, comments: [] }] },
        { headRefOid: previous },
        { baseRefName: "other" },
        { state: "CLOSED" },
    ]) {
        const result = await observe({ ...snapshot, ...changed });
        assert.equal(result.status, "needs-owner-decision");
        assert.equal(result.repairAttempts, 0);
    }
    const pending = await observe({ ...snapshot, statusCheckRollup: [{ name: "CI", status: "IN_PROGRESS" }] });
    assert.equal(pending.ci.status, "pending");
    assert.notEqual(pending.status, "ci-failed");
    assert.equal(pending.continuation, null, "read-only observation must not advertise a mutation-capable standalone continuation");
    const unknown = await observe({ ...snapshot, statusCheckRollup: [] });
    assert.equal(unknown.ci.status, "unknown");
    assert.notEqual(unknown.status, "ready-for-human-merge");
    const failed = await observe({ ...snapshot, statusCheckRollup: [{ name: "CI", status: "COMPLETED", conclusion: "FAILURE" }] });
    assert.equal(failed.ci.status, "failed");
    assert.equal(failed.status, "ci-failed");
    for (const changed of [
        { headRefOid: previous },
        { headRefName: "other" },
        { baseRefName: "other" },
        { number: 43 },
        { state: "CLOSED" },
    ]) {
        const result = await observe({
            ...snapshot, ...changed,
            statusCheckRollup: [{ name: "CI", status: "COMPLETED", conclusion: "FAILURE" }],
        });
        assert.equal(result.status, "needs-owner-decision");
        assert.equal(result.ci.status, "unknown");
        assert.equal(result.ci.headSha, null);
        assert.equal(result.repairAttempts, 0);
    }
});

test("read-only observer revalidates the reviewed head after polling", async () => {
    const trustedRepository = { provider: "github", hostname: "github.com", slug: "example/project",
        localPath: ".", baseBranch: "dev", headSha: current, headBranch: "feature" };
    const url = "https://github.com/example/project/pull/42";
    let reads = 0;
    const result = await runPrObserver({
        args: { prUrl: url, options: { maxCiPollAttempts: 1, ciPollIntervalMs: 0 } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("Unexpected agent"); },
    }, {
        observeOnly: true, trustedRepository, exec: () => "", sleep: async () => {},
        fetchPrSnapshot: () => ({
            provider: "github", number: 42, url, state: "OPEN", baseRefName: "dev",
            headRefName: "feature", headRefOid: ++reads === 1 ? current : previous,
            mergeStateStatus: "CLEAN", reviewThreadsComplete: true, unresolvedReviewThreadCount: 0,
            statusCheckRollup: [reads === 1
                ? { name: "CI", status: "IN_PROGRESS" }
                : { name: "CI", status: "COMPLETED", conclusion: "FAILURE" }],
        }),
    });
    assert.equal(reads, 2);
    assert.equal(result.status, "needs-owner-decision");
    assert.equal(result.ci.status, "unknown");
    assert.equal(result.ci.headSha, null);
    assert.match(result.summary, /head changed/);
});

test("cancelling a quiet read-only observer interrupts polling without a final status or another read", async () => {
    const controller = new AbortController();
    const url = "https://github.com/example/project/pull/42";
    let reads = 0;
    await assert.rejects(runPrObserver({
        signal: controller.signal,
        args: { prUrl: url, options: { maxCiPollAttempts: 1, ciPollIntervalMs: 200 } },
        step: async (_key, producer) => producer(),
        agent: async () => { throw new Error("Unexpected agent"); },
    }, {
        observeOnly: true,
        trustedRepository: { provider: "github", hostname: "github.com", slug: "example/project",
            localPath: ".", baseBranch: "dev", headSha: current, headBranch: "feature" },
        exec: () => "",
        fetchPrSnapshot: () => {
            reads++;
            setTimeout(() => controller.abort(), 5);
            return {
                provider: "github", number: 42, url, state: "OPEN", baseRefName: "dev",
                headRefName: "feature", headRefOid: current, mergeStateStatus: "CLEAN",
                reviewThreadsComplete: true, unresolvedReviewThreadCount: 0,
                statusCheckRollup: [{ name: "CI", status: "IN_PROGRESS" }],
            };
        },
    }), { name: "AbortError" });
    assert.equal(reads, 1);
});
