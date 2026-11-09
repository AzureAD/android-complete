// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { milestoneFromProgress } from "./milestones.mjs";
import {
    buildAdoPullRequestPublicationScript, collectBuildReceiptOnDisk, discoverChangedFilesFromGit, ensureIsolatedTargetWorktree, formatPrBody, isBuilderShortCircuitedToCi, normalizeImplementationResponse, parseAllowedGradleVerificationCommand, runCodeFeature, runLocalVerificationGate, validateImplementation,
    BUILDER_AGENT, EVIDENCE_AGENT, REVIEWER_AGENT, rubric,
} from "./workflow.mjs";
import {
    ciEvidenceFromSnapshot, ciWaitingSummary, fetchPullRequestSnapshot, parsePullRequestUrl, publishObserverStatus, refreshStaleAdoBuildPolicies, runPrObserver,
} from "./observer.mjs";
import { ciVerificationSummary, deliveryOutcome, localVerificationSummary, pendingVerification, verificationFromGate, verificationSummary } from "./verification.mjs";
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

test("verification wording separates failed, deferred, unconfirmed and executed checks without raw labels", () => {
    const cases = [
        ["passed", "Local verification passed."],
        ["failed", "Local verification failed."],
        ["deferred", "Local verification is incomplete; remaining checks need CI."],
        ["unverified", "Local verification has not been confirmed."],
    ];
    for (const [status, expected] of cases) {
        const verification = { local: { status, evidence: ["Successful command: compile", "Specific task diagnostic."] }, ci: { status: "not-observed" } };
        assert.ok(localVerificationSummary(verification).startsWith(expected));
        if (["failed", "deferred"].includes(status)) assert.match(localVerificationSummary(verification), /Specific task diagnostic/);
        else assert.doesNotMatch(localVerificationSummary(verification), /Specific task diagnostic/);
        assert.match(verificationSummary(verification), /CI has not been checked yet/);
    }
    assert.equal(ciVerificationSummary({ ci: { status: "pending" } }), "Waiting for CI results for the current PR head.");
    assert.equal(ciVerificationSummary({ ci: { status: "failed", headSha: "abc123" } }), "CI failed for the current PR head.");
    assert.equal(ciVerificationSummary({ ci: { status: "unknown" } }), "CI results for the current PR head could not be confirmed.");
    assert.equal(ciVerificationSummary({ ci: { status: "passed", headSha: "abc123" } }), "CI passed for the current PR head.");
    assert.equal(ciVerificationSummary({ ci: { status: "passed" } }), "CI reports a pass, but its PR head has not been confirmed.");
    assert.equal(ciVerificationSummary({ ci: { status: "failed" } }), "CI reports a failure, but its PR head has not been confirmed.");
});

test("local diagnostic summaries are bounded without changing full failed or deferred receipts", () => {
    for (const status of ["failed", "deferred"]) {
        const reason = `SDK location not found. ${"Full build diagnostic. ".repeat(10_000)}`;
        const receipt = { local: { status, evidence: ["Successful command: compile", reason] } };
        const summary = localVerificationSummary(receipt);
        assert.ok(summary.length <= 500);
        assert.match(summary, /SDK location not found/);
        assert.ok(summary.endsWith("..."));
        assert.equal(receipt.local.evidence[1], reason);
    }
});

test("long command narration cannot hide the recorded verification stopping or failure cause", () => {
    for (const [status, cause] of [
        ["deferred", "Stopped the running verification shell at the 12-minute cap with zero retries; local verification was short-circuited to remote CI."],
        ["failed", "Execution failed for task :app:compileProductionDebugKotlin: unresolved reference in the changed file."],
    ]) {
        const narration = `Builder reported: Authored five focused Kotlin test classes. Attempted the combined Gradle command with ${"--no-daemon --max-workers=2 :app:compileProductionDebugKotlin ".repeat(100)}. Completed evidence: 41 ECS tests passed. ${cause} Remaining checks need CI.`;
        const receipt = { local: { status, commands: [], evidence: [narration] }, ci: { status: "not-observed" } };
        const before = structuredClone(receipt);
        const summary = localVerificationSummary(receipt);
        assert.ok(summary.length <= 500);
        assert.ok(summary.includes(cause), summary);
        assert.match(summary, /Builder reported:/);
        assert.doesNotMatch(summary, /Local verification passed|--max-workers|41 ECS tests passed/);
        assert.deepEqual(receipt, before);
    }
});

test("repeated CI updates retain failed and deferred local reasons without changing the compact marker schema", () => {
    const parsed = parsePullRequestUrl("https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42");
    for (const status of ["failed", "deferred"]) {
        const verification = {
            local: { status, headSha: "a".repeat(40), evidence: ["SDK location not found. Device verification needs authorized CI."] },
            ci: { status: "not-observed", headSha: null },
        };
        let description = `## Validation & Testing\n${verificationSummary(verification)}\nHuman details.\n${renderVerificationBlock(verification)}`;
        for (const ciStatus of ["pending", "passed", "failed", "passed"]) {
            const ci = { status: ciStatus, headSha: "a".repeat(40) };
            description = publishObserverStatus(parsed, snapshot({ description }), "status", { exec: () => "", ci });
            const line = description.split("\n").find((value) => value.startsWith("Local verification"));
            assert.equal(line, verificationSummary({ ...verification, ci }));
            assert.match(description, /Human details\./);
            const marker = parseMarkerBlock(description, verificationMarkers);
            assert.deepEqual(Object.keys(marker.local).sort(), ["headSha", "status"]);
            assert.equal(marker.local.status, status);
            assert.equal(marker.ci.status, ciStatus);
        }
        assert.deepEqual(verification.local.evidence, ["SDK location not found. Device verification needs authorized CI."]);
    }
});

test("CI waiting wording claims running or queued only from the actual current-head snapshot", () => {
    assert.equal(ciWaitingSummary(snapshot({ statusCheckRollup: [{ status: "queued" }] })), "CI checks are queued.");
    assert.equal(ciWaitingSummary(snapshot({ statusCheckRollup: [{ status: "in_progress" }] })), "CI checks are running.");
    assert.equal(ciWaitingSummary(snapshot({ statusCheckRollup: [{ status: "queued" }, { status: "in_progress" }] })), "CI has running and queued checks.");
    assert.equal(ciWaitingSummary(snapshot({ statusCheckRollup: [{ state: "pending" }] })), "Waiting for CI results for the current PR head.");
    assert.equal(ciWaitingSummary(snapshot({ snapshotHeadChanged: true })), "The PR head changed; waiting for fresh CI results.");
    assert.match(ciWaitingSummary(snapshot(), "b".repeat(40)), /Waiting for the CI provider to recognize pushed commit b+/);
    assert.equal(ciWaitingSummary(snapshot({
        provider: "ado", statusCheckRollup: [],
        prBuilds: [{ status: "inProgress", sourceHeadSha: "b".repeat(40) }],
    })), "Waiting for CI results for the current PR head.", "An old build cannot establish current activity");
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

test("ADO metadata deduplicates exact WiClose lines without changing other narrative", () => {
    const wi = "WiClose: https://identitydivision.visualstudio.com/Engineering/_workitems/edit/123";
    const result = fitAdoDescription(`Human details\r\n${wi}\r\nOther details\r\n${wi}\r\n`);
    assert.equal(result.split(/\r?\n/).filter((line) => line === wi).length, 1);
    assert.equal(result, `Human details\r\n${wi}\r\nOther details\r\n`);
});

test("final managed narrative and JSON show the same current verification state", () => {
    const wi = "WiClose: https://identitydivision.visualstudio.com/Engineering/_workitems/edit/123";
    const description = `## Validation & Testing\nLocal verification: unverified. Current-head CI: not-observed.\nHuman details stay unchanged.\n${wi}\n${wi}\n` +
        renderVerificationBlock(pendingVerification());
    const updated = publishObserverStatus(
        parsePullRequestUrl("https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42"),
        snapshot({ description }), "status", { exec: () => "" },
    );
    assert.match(updated, /Local verification has not been confirmed\. CI passed for the current PR head\./);
    assert.match(updated, /Human details stay unchanged\./);
    assert.equal(parseMarkerBlock(updated, verificationMarkers).ci.status, "passed");
    assert.equal(updated.split(/\r?\n/).filter((line) => line === wi).length, 1);
    const failed = publishObserverStatus(
        parsePullRequestUrl("https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42"),
        snapshot({ description: updated }), "status", { exec: () => "", ci: { status: "failed", headSha: "a".repeat(40) } },
    );
    assert.match(failed, /CI failed for the current PR head\./);
    assert.doesNotMatch(failed, /CI passed for the current PR head/);
    assert.match(failed, /Human details stay unchanged\./);
    assert.equal(parseMarkerBlock(failed, verificationMarkers).ci.status, "failed");
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
        description: body, provider: "ado", prBuilds: [{ id: 1, status: "completed", result: "succeeded", sourceHeadSha: "a".repeat(40) }],
    }), block, { exec: () => "" });
    assert.ok(updated.length <= 4000, `Final status length: ${updated.length}`);
    assert.equal(parseMarkerBlock(updated, verificationMarkers).ci.status, "passed");
    assert.equal(parseMarkerBlock(updated, statusMarkers).prUrl, url);
});

test("same-session recovery hot-loads changed status, verification, workflow, and observer dependencies", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "workflow-hot-recovery-"));
    try {
        for (const name of ["workflow.mjs", "observer.mjs", "status.mjs", "verification.mjs", "milestones.mjs"]) {
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

async function assertCommittedPublicationReplay({ fixRound = false, tamper = false, verifyLocally = false, extraCommit = false, commitInjection = false, indexReset = false } = {}) {
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
        let pushes = 0;
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
            if (command === "git" && args.includes("push")) { pushes++; return ""; }
            if (command === "git" && args.includes("fetch")) return "";
            if (commitInjection && command === "git" && args[0] === "commit") {
                writeFileSync(path.join(repo, "HookChange.kt"), "class UnreviewedHookChange\n");
                git("add", "HookChange.kt");
            }
            const output = execFileSync(command, args, { ...options, encoding: "utf8", stdio: "pipe" }).trim();
            if (indexReset && command === "git" && args[0] === "add" && args.at(-1) === "FooTest.kt" &&
                !options?.env?.GIT_INDEX_FILE) git("read-tree", "HEAD");
            return output;
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
        await assert.rejects(runCodeFeature(make(results), { exec }),
            commitInjection || indexReset ? /Reviewed implementation changed/ : /Fixture publication unavailable/);
        if (commitInjection || indexReset) {
            assert.equal(pushes, 0, "A commit-time index mutation must be caught before any push");
            if (commitInjection) assert.match(git("show", "HEAD:HookChange.kt"), /UnreviewedHookChange/);
            else assert.equal(git("show", "HEAD:README"), "baseline");
            return;
        }
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
        if (extraCommit) {
            writeFileSync(path.join(repo, "Unreviewed.kt"), "class Unreviewed\n");
            git("add", "Unreviewed.kt");
            git("commit", "--quiet", "-m", "Unrelated external commit");
            const externalHead = git("rev-parse", "HEAD");
            const pushesBefore = pushes;
            await assert.rejects(runCodeFeature(replay, { exec }), /Reviewed implementation changed/);
            assert.equal(pushes, pushesBefore, "Cached source review must not push an extra commit outside the old file list");
            assert.equal(git("rev-parse", "HEAD"), externalHead);
            assert.equal(replay.calls.length, 0);
            return;
        }
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
test("publication-error replay rejects an unrelated committed file outside the reviewed implementation list", () =>
    assertCommittedPublicationReplay({ extraCommit: true }));
test("publication verifies the actual committed tree before pushing commit-time index mutations", () =>
    assertCommittedPublicationReplay({ commitInjection: true }));
test("publication cannot push the baseline if the live index loses reviewed staging", () =>
    assertCommittedPublicationReplay({ indexReset: true }));

async function assertReviewedSourcePresence({ action = "modify", mutate = () => {}, changed = true, reviewedIndex = false } = {}) {
    const root = mkdtempSync(path.join(os.tmpdir(), "reviewed-source-presence-"));
    const repo = path.join(root, "common");
    try {
        mkdirSync(repo);
        const git = (...args) => execFileSync("git", args, { cwd: repo, encoding: "utf8", stdio: "pipe" }).trim();
        git("init", "--quiet");
        git("config", "user.name", "Workflow Fixture");
        git("config", "user.email", "fixture@example.invalid");
        writeFileSync(path.join(repo, "Foo.kt"), "// Copyright Microsoft\nclass Foo\n");
        git("add", "Foo.kt");
        git("commit", "--quiet", "-m", "baseline");
        git("update-ref", "refs/remotes/origin/dev", "HEAD");
        git("remote", "add", "origin", "https://github.com/AzureAD/microsoft-authentication-library-common-for-android.git");
        const baseline = git("rev-parse", "HEAD");
        if (reviewedIndex) {
            writeFileSync(path.join(repo, "StagedOnly.kt"), "class ReviewedIndexContent\n");
            git("add", "StagedOnly.kt");
            rmSync(path.join(repo, "StagedOnly.kt"));
        }
        const originalIndex = git("write-tree");
        const build = implementation();
        build.targets[0].files.unshift({
            path: "Foo.kt", action,
            content: action === "delete" ? "" : "// Copyright Microsoft\nclass Foo { val reviewed = true }\n",
            summary: "Reviewed source change",
        });
        const evidence = {
            approvalVerification: { verified: true, revision: "v1", reference: "approval", rationale: "Approved" },
            references: [{ id: "E1", repository: "common", revision: baseline, path: "Foo.kt", locator: "Foo" }],
            facts: [{ statement: "Exists", referenceIds: ["E1"] }], conflicts: [], assumptions: [], unknowns: [], blockingUnknowns: [],
        };
        const review = {
            criteria: rubric.map((id) => ({ id, pass: true })),
            acceptanceCriteria: [{ targetId: "common", criterion: "Tests pass", passed: true, evidence: "Source reviewed only" }],
            findings: [], blockingUnknowns: [],
        };
        const publicationCalls = [];
        const exec = (command, args, options) => {
            if (command === "gh") {
                publicationCalls.push([command, args]);
                if (args[0] === "auth" && args[1] === "status") return "Logged in to github.com account fixture (keyring)";
                if (args[0] === "auth" && args[1] === "token") return "fixture-token";
                if (args[0] === "pr" && args[1] === "create") return prUrl;
                return "";
            }
            if (command === "git" && args.includes("fetch")) return "";
            if (command === "git" && !options?.env?.GIT_INDEX_FILE &&
                (args.includes("add") || args.includes("commit") || args.includes("push"))) {
                publicationCalls.push([command, args]);
                if (args.includes("push")) return "";
            }
            return execFileSync(command, args, { ...options, encoding: "utf8", stdio: "pipe" }).trim();
        };
        const journal = new Map();
        let interrupt = true;
        const make = (results) => {
            const context = ctx(results);
            context.args = { task, options: { workspaceRoot: root, createPullRequests: true, verifyPrerequisites: false } };
            context.runId = "reviewed-source-presence";
            context.step = async (key, producer, options) => {
                if (options?.volatile) return producer();
                if (!journal.has(key)) journal.set(key, JSON.parse(JSON.stringify(await producer())));
                if (interrupt && key.endsWith("-reviewed-source")) throw new DOMException("Paused after reviewed source was saved", "AbortError");
                return JSON.parse(JSON.stringify(journal.get(key)));
            };
            return context;
        };
        const first = make([evidence, build, review]);
        const agent = first.agent;
        first.agent = (prompt, options) => {
            if (options.agent === REVIEWER_AGENT && reviewedIndex) {
                const data = JSON.parse(prompt.slice(prompt.lastIndexOf("DATA:\n") + 6));
                assert.match(data.diffsByTarget.common, /StagedOnly.kt/);
                assert.match(data.diffsByTarget.common, /ReviewedIndexContent/);
                assert.equal(data.implementation.targets[0].files.some((file) => file.path === "StagedOnly.kt"), false);
            }
            return agent.call(first, prompt, options);
        };
        await assert.rejects(runCodeFeature(first, { exec }), { name: "AbortError" });
        assert.equal(first.calls.length, 3);
        assert.equal(publicationCalls.length, 0, "Interruption must occur before staging or publication");
        assert.equal(git("rev-parse", "HEAD"), baseline);
        assert.equal(git("write-tree"), originalIndex, "Computing the full reviewed tree must not mutate the caller's index");
        const prepared = [...journal.entries()].find(([key]) => key.endsWith("-reviewed-source"))[1];
        assert.equal(prepared.recommendation, "publication-ready");
        assert.equal(prepared.implementation.targets[0].files.find((file) => file.path === "Foo.kt").action, action);
        mutate(path.join(repo, "Foo.kt"), git, repo);
        const beforeReplay = git("status", "--porcelain");
        const replayHead = git("rev-parse", "HEAD");
        interrupt = false;
        const replay = make([]);
        if (changed) {
            await assert.rejects(runCodeFeature(replay, { exec }), /Reviewed implementation changed/);
            assert.equal(publicationCalls.length, 0, "Changed source cannot reach staging, commit, push or PR creation");
            assert.equal(git("rev-parse", "HEAD"), replayHead);
            assert.equal(git("status", "--porcelain"), beforeReplay, "Recovery must not restore or overwrite external changes");
        } else {
            const result = await runCodeFeature(replay, { exec });
            assert.equal(result.pullRequests[0].url, prUrl);
            assert.ok(publicationCalls.some(([command, args]) => command === "git" && args.includes("commit")));
            assert.equal(git("ls-files", "Foo.kt"), "", "The reviewed intentional deletion must still publish");
            if (reviewedIndex) assert.match(git("show", "HEAD:StagedOnly.kt"), /ReviewedIndexContent/);
        }
        assert.equal(replay.calls.length, 0, "A source-binding rejection must not silently launch another builder or reviewer");
    } finally { rmSync(root, { recursive: true, force: true }); }
}

test("publication rejects a tracked modify file removed after the reviewed-source checkpoint", () =>
    assertReviewedSourcePresence({ mutate: (file) => rmSync(file) }));
test("publication rejects an intentionally deleted file recreated empty after review", () =>
    assertReviewedSourcePresence({ action: "delete", mutate: (file) => writeFileSync(file, "") }));
test("an unchanged intentional deletion can resume from the reviewed-source checkpoint", () =>
    assertReviewedSourcePresence({ action: "delete", changed: false }));
test("publication rejects an unrelated staged file added after the reviewed-source checkpoint", () =>
    assertReviewedSourcePresence({ mutate: (_file, git, repo) => {
        writeFileSync(path.join(repo, "Unreviewed.kt"), "class Unreviewed\n");
        git("add", "Unreviewed.kt");
    } }));
test("publication binds index-only content even when the working file is restored", () =>
    assertReviewedSourcePresence({ mutate: (_file, git, repo) => {
        const file = path.join(repo, "IndexOnly.kt");
        writeFileSync(file, "class UnreviewedIndex\n");
        git("add", "IndexOnly.kt");
        rmSync(file);
    } }));
test("the source reviewer sees the complete publication diff including staged-only files outside the builder list", () =>
    assertReviewedSourcePresence({ action: "delete", changed: false, reviewedIndex: true }));

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

test("mixed local compile and deferred lint/device tasks cannot produce a passed verification receipt", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "mixed-verification-"));
    try {
        writeFileSync(path.join(root, "gradlew.bat"), "");
        for (const tasks of [
            [":app:lintProductionDebug"],
            [":app:connectedProductionDebugAndroidTest"],
            [":app:lintProductionDebug", ":app:connectedCheck"],
        ]) {
            const command = [".\\gradlew.bat", ":app:compileProductionDebugKotlin", ...tasks, "--no-daemon", "-x", "lint"].join(" ");
            const calls = [];
            const gate = runLocalVerificationGate({
                repoRoot: root, target: { id: "fixture", verificationCommands: [command] },
                exec: (executable, args) => { if (/gradlew/i.test(executable)) calls.push(args); return ""; },
            });
            assert.deepEqual(calls, [[":app:compileProductionDebugKotlin", "--no-daemon", "-x", "lint"]]);
            assert.equal(gate.outcomes.passed.length, 1);
            assert.equal(gate.outcomes.deferred.length, tasks.length);
            for (const task of tasks) assert.ok(gate.outcomes.deferred.some((entry) => entry.command.includes(task)));
            assert.equal(verificationFromGate(gate).local.status, "deferred");
            assert.deepEqual(parseAllowedGradleVerificationCommand(command).args, calls[0]);
        }
    } finally { rmSync(root, { recursive: true, force: true }); }
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

test("repair messages distinguish an unchanged pushed head from a real source commit", async () => {
    for (const disposition of ["unchanged", "changed", "unconfirmed"]) {
        const changed = disposition === "changed";
        const originalHead = "a".repeat(40);
        const pushedHead = changed ? "b".repeat(40) : originalHead;
        const repair = { verdict: "pass", summary: changed ? "Fixed the reported test." : "No source changes are warranted.", evidence: [] };
        const context = ctx([repair, passedReview], { maxIterations: 1, pushRepairs: true });
        const logs = [];
        const commands = [];
        context.log = (text) => logs.push(text);
        context.step = async (key, producer) => {
            const result = await producer();
            return /-push-repair-\d+$/.test(key)
                ? { ...result, pushedSha: disposition === "unconfirmed" ? "" : pushedHead } : result;
        };
        let reads = 0;
        const result = await runPrObserver(context, {
            exec: (command, args) => { commands.push({ command, args }); return ""; },
            fetchPrSnapshot: () => ++reads === 1
                ? snapshot({ headRefOid: originalHead, prBuilds: [{ id: 77, status: "completed", result: "failed", failedTasks: [{ name: "Compile", excerpt: ["FooTest.kt:12 Unresolved reference"] }] }] })
                : snapshot({ headRefOid: pushedHead }),
        });
        const milestones = logs.map((text) => milestoneFromProgress({ kind: "log", text })).filter(Boolean);
        const assessed = milestones.find((item) => /-authored$/.test(item.id));
        const pushed = milestones.find((item) => /-pushed$/.test(item.id));
        assert.doesNotMatch(assessed.summary, /Repair authored/);
        assert.ok(commands.some(({ command, args }) => command === "git" && args.includes("push")));
        assert.equal(context.calls.length, 2);
        assert.equal(result.ci.headSha, pushedHead);
        if (changed) {
            assert.match(pushed.summary, /Repair pushed/);
            assert.doesNotMatch(pushed.summary, /unchanged/);
        } else if (disposition === "unchanged") {
            assert.match(pushed.summary, /Push completed.*source head is unchanged/);
            assert.doesNotMatch(pushed.title + pushed.summary, /Repair pushed|repair .* pushed/i);
        } else {
            assert.match(pushed.summary, /resulting source head could not be confirmed/);
            assert.doesNotMatch(pushed.summary, /Repair pushed|source head is unchanged/);
        }
    }
});

test("embedded observer stages only repair files, preserves local SDK config and publishes its deletion", async () => {
    for (const kind of ["no-change", "source", "delete-config", "staged-config", "staged-only-addition", "staged-only-edit"]) {
        const root = mkdtempSync(path.join(os.tmpdir(), "observer-explicit-staging-"));
        const source = path.join(root, "source");
        const repo = path.join(root, "common");
        try {
            mkdirSync(source);
            const git = (...args) => execFileSync("git", args, { cwd: source, encoding: "utf8", stdio: "pipe" }).trim();
            git("init", "--quiet");
            git("config", "user.name", "Workflow Fixture");
            git("config", "user.email", "fixture@example.invalid");
            writeFileSync(path.join(source, "Foo.kt"), "class Foo\n");
            writeFileSync(path.join(source, "local.properties"), "sdk.dir=fixture-sdk\n");
            writeFileSync(path.join(source, "README"), "baseline\n");
            git("add", "Foo.kt", "README", ...(["delete-config", "staged-only-edit"].includes(kind) ? ["local.properties"] : []));
            git("commit", "--quiet", "-m", "baseline");
            git("update-ref", "refs/remotes/origin/dev", "HEAD");
            git("remote", "add", "origin", "https://github.com/AzureAD/microsoft-authentication-library-common-for-android.git");
            git("worktree", "add", "--quiet", "-b", "feature", repo, "HEAD");
            const original = git("rev-parse", "HEAD");
            const localContent = kind === "staged-only-edit" ? "sdk.dir=updated-fixture-sdk\n" : "sdk.dir=fixture-sdk\n";
            writeFileSync(path.join(repo, "local.properties"), localContent);
            mkdirSync(path.join(repo, "nested"));
            writeFileSync(path.join(repo, "nested", "local.properties"), "sdk.dir=fixture-sdk\n");
            if (kind.startsWith("staged-")) execFileSync("git", ["add", "local.properties"], { cwd: repo });
            const initialIndex = execFileSync("git", ["write-tree"], { cwd: repo, encoding: "utf8" }).trim();
            const commands = [];
            const exec = (command, args, options) => {
                commands.push({ command, args });
                if (command === "gh") return "";
                if (command === "git" && args.includes("push")) return "";
                return execFileSync(command, args, { ...options, encoding: "utf8", stdio: "pipe" }).trim();
            };
            let reads = 0;
            const context = ctx([{ verdict: "pass", summary: "Tactical assessment complete.", evidence: [] }, passedReview], { workspaceRoot: root, pushRepairs: true });
            const agent = context.agent;
            context.agent = async (prompt, options) => {
                if (options.agent === BUILDER_AGENT) {
                    if (kind === "source" || kind === "staged-config") writeFileSync(path.join(repo, "Foo.kt"), "class Foo { val fixed = true }\n");
                    if (kind === "delete-config") {
                        rmSync(path.join(repo, "local.properties"));
                        writeFileSync(path.join(repo, ".gitignore"), "/local.properties\n");
                    }
                }
                return agent.call(context, prompt, options);
            };
            const run = runPrObserver(context, {
                exec,
                fetchPrSnapshot: () => snapshot({
                    headRefOid: execFileSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).trim(),
                    ...(++reads === 1 ? { prBuilds: [{ id: 77, status: "completed", result: "failed", failedTasks: [{ name: "Compile", excerpt: ["Foo.kt:12: error: Unresolved reference: fixture"] }] }] } : {}),
                }),
            });
            if (kind.startsWith("staged-")) {
                await assert.rejects(run, /local SDK configuration.*staged/i);
                assert.equal(commands.filter((item) => item.command === "git" && item.args.includes("push")).length, 0);
                assert.equal(commands.filter((item) => item.command === "git" && item.args[0] === "commit").length, 0);
                assert.equal(execFileSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).trim(), original);
                assert.equal(execFileSync("git", ["write-tree"], { cwd: repo, encoding: "utf8" }).trim(), initialIndex);
                assert.equal(readFileSync(path.join(repo, "local.properties"), "utf8"), localContent);
                assert.match(execFileSync("git", ["diff", "--cached", "--name-only"], { cwd: repo, encoding: "utf8" }), /local.properties/);
            } else {
                const result = await run;
                const head = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).trim();
                assert.equal(context.calls.length, 2);
                assert.equal(result.headSha, head);
                assert.equal(commands.some((item) => item.command === "git" && item.args.includes("-A")), false);
                if (kind === "no-change") {
                    assert.equal(head, original);
                    assert.equal(commands.some((item) => item.command === "git" && item.args[0] === "commit"), false);
                } else {
                    assert.notEqual(head, original);
                    const delta = execFileSync("git", ["diff", "--name-status", original, head], { cwd: repo, encoding: "utf8" });
                    assert.equal(delta.includes("nested/local.properties"), false);
                    if (kind === "delete-config") assert.match(delta, /D\s+local.properties/);
                    else assert.doesNotMatch(delta, /local.properties/);
                }
            }
        } finally { rmSync(root, { recursive: true, force: true }); }
    }
});

test("SDK discovery permits tracked deletions but never additions or modifications", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "sdk-discovery-policy-"));
    try {
        mkdirSync(path.join(root, ".git"));
        mkdirSync(path.join(root, "nested"));
        writeFileSync(path.join(root, "local.properties"), "sdk.dir=fixture\n");
        writeFileSync(path.join(root, "nested", "local.properties"), "sdk.dir=fixture\n");
        for (const status of ["??", " A", " M", " D", "D "]) {
            const result = discoverChangedFilesFromGit(root, () => `${status} local.properties\n${status} nested/local.properties`, { allowPipelineEdits: true });
            assert.equal(result.files.length, status.includes("D") ? 2 : 0, status);
            assert.ok(result.files.every((file) => file.action === "delete"));
        }
        const value = implementation();
        value.targets[0].files.push({ path: "local.properties", action: "create", content: "sdk.dir=fixture", summary: "Local SDK" });
        assert.ok(validateImplementation(value, task).some((line) => /local SDK configuration/i.test(line)));
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("checkout transport recovery keeps the source-repair allowance and needs no repair agents", async () => {
    const current = "a".repeat(40);
    const failed = snapshot({
        provider: "ado",
        prBuilds: [{ id: 77, sourceHeadSha: current, definition: "Android Authenticator PR Checks", status: "completed", result: "failed",
            failedTasks: [{ name: "Checkout", issues: ["Git fetch failed"], excerpt: ["curl 56 Connection reset by peer", "fatal: fetch-pack: invalid index-pack output"] }] }],
    });
    const pending = snapshot({ provider: "ado", prBuilds: [{ id: 78, sourceHeadSha: current, status: "inProgress" }] });
    for (const recover of [true, false]) {
        const context = ctx([], { maxCiPollAttempts: 2, ciPollIntervalMs: 0 });
        context.args.prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
        let reads = 0;
        let retries = 0;
        const result = await runPrObserver(context, {
            exec: () => "", sleep: async () => {},
            retryFailedBuilds: () => { retries++; return true; },
            fetchPrSnapshot: () => recover && ++reads > 1 ? pending : failed,
        });
        assert.equal(retries, 1);
        assert.equal(context.calls.length, 0);
        assert.equal(result.repairAttempts, 0);
        if (recover) {
            assert.equal(result.status, "waiting-for-ci");
            assert.equal(result.continuation.args.maxIterations, 2);
        } else {
            assert.equal(result.blockerKind, "ci-infrastructure");
            assert.match(result.summary, /checkout.*retry|retry.*checkout/i);
        }
    }
});

test("checkout retry cannot hide a compiler defect, unconfirmed retry or disabled mutation", async () => {
    const base = snapshot({ provider: "ado", prBuilds: [{ id: 77, sourceHeadSha: "a".repeat(40), definition: "Checks", status: "completed", result: "failed",
        failedTasks: [{ name: "Checkout", excerpt: ["curl 56 Connection reset by peer"] }] }] });
    for (const kind of ["compiler", "retry-failed", "readonly", "no-wait"]) {
        const failed = structuredClone(base);
        if (kind === "compiler") failed.prBuilds[0].failedTasks.push({ name: "Compile", excerpt: ["Unresolved reference: launch"] });
        const context = ctx([], { maxCiPollAttempts: kind === "no-wait" ? 0 : 1, ciPollIntervalMs: 0, pushRepairs: kind !== "readonly" });
        context.args.prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
        context.args.maxIterations = 0;
        let retries = 0;
        const result = await runPrObserver(context, {
            exec: () => "", sleep: async () => {},
            retryFailedBuilds: () => { retries++; return false; },
            fetchPrSnapshot: () => failed,
        });
        assert.equal(context.calls.length, 0);
        assert.notEqual(result.ci.status, "passed");
        assert.equal(retries, kind === "retry-failed" ? 1 : 0);
        assert.equal(result.status, kind === "compiler" ? "max-iterations-reached" : "needs-owner-decision");
    }
});

test("unfinished current-head source failures prevent checkout-only recovery", async () => {
    for (const failure of [
        { name: "Compile", excerpt: ["Unresolved reference: launchForResult"] },
        { name: "Unit tests", failedTests: [{ testName: "FooTest", errorMessage: "Expected tenant A but received B" }] },
    ]) {
        const context = ctx([], { maxCiPollAttempts: 1, ciPollIntervalMs: 0 });
        context.args.prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
        context.args.maxIterations = 0;
        let retries = 0;
        const observed = snapshot({ provider: "ado", prBuilds: [
            { id: 77, sourceHeadSha: "a".repeat(40), definition: "Checks", status: "completed", result: "failed",
                failedTasks: [{ name: "Checkout", excerpt: ["curl 56 Connection reset by peer"] }] },
            { id: 78, sourceHeadSha: "a".repeat(40), definition: "Checks", status: "inProgress", result: null, failedTasks: [failure] },
        ] });
        const result = await runPrObserver(context, {
            exec: () => "", fetchPrSnapshot: () => observed,
            retryFailedBuilds: () => { retries++; return true; },
        });
        assert.equal(retries, 0);
        assert.equal(context.calls.length, 0);
        assert.equal(result.status, "max-iterations-reached");
        assert.equal(result.ci.status, "failed");
        assert.ok(result.actionableFindings.some((finding) => finding.includes(failure.name)));
    }
});

test("checkout retries target only failed current-head builds, not unfinished, advisory or stale builds", async () => {
    const head = "a".repeat(40);
    const context = ctx([], { maxCiPollAttempts: 1, ciPollIntervalMs: 0 });
    context.args.prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
    const observed = snapshot({ provider: "ado", prBuilds: [
        { id: 77, sourceHeadSha: head, definition: "Checks", status: "completed", result: "failed",
            failedTasks: [{ name: "Checkout", excerpt: ["curl 56 Connection reset by peer"] }] },
        { id: 78, sourceHeadSha: head, definition: "Checks", status: "inProgress", result: null, failedTasks: [] },
        { id: 79, sourceHeadSha: head, definition: "AI PR Review", status: "inProgress", result: null,
            failedTasks: [{ name: "Compile", excerpt: ["Unresolved reference: advisoryFixture"] }] },
        { id: 80, sourceHeadSha: "b".repeat(40), definition: "Checks", status: "inProgress", result: null,
            failedTasks: [{ name: "Compile", excerpt: ["Unresolved reference: staleFixture"] }] },
    ] });
    let retries = 0;
    const result = await runPrObserver(context, {
        exec: () => "", sleep: async () => {}, fetchPrSnapshot: () => observed,
        retryFailedBuilds: (_pr, supplied, options) => {
            retries++;
            assert.deepEqual(supplied.prBuilds.map((build) => build.id), [77]);
            assert.equal(options.stageOnly, true);
            return true;
        },
    });
    assert.equal(retries, 1);
    assert.equal(context.calls.length, 0);
    assert.equal(result.repairAttempts, 0);
    assert.equal(result.blockerKind, "ci-infrastructure");
});

test("cancellation during checkout recovery propagates without a success notice or follow-up read", async () => {
    const controller = new AbortController();
    const context = ctx([], { maxCiPollAttempts: 1 });
    context.args.prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
    context.signal = controller.signal;
    const logs = [];
    context.log = (line) => logs.push(line);
    let reads = 0;
    await assert.rejects(runPrObserver(context, {
        exec: () => "",
        fetchPrSnapshot: () => {
            reads++;
            return snapshot({ provider: "ado", prBuilds: [{ id: 77, sourceHeadSha: "a".repeat(40), status: "completed", result: "failed",
                failedTasks: [{ name: "Checkout", excerpt: ["Connection reset by peer"] }] }] });
        },
        retryFailedBuilds: () => { controller.abort(); return true; },
    }), { name: "AbortError" });
    assert.equal(reads, 1);
    assert.equal(context.calls.length, 0);
    assert.equal(logs.some((line) => /CI checkout retry requested/.test(line)), false);
});

test("journal replay cannot request another checkout retry for a different build on the same source head", async () => {
    const journal = new Map();
    let retries = 0;
    const make = () => {
        const context = ctx([], { maxCiPollAttempts: 1, ciPollIntervalMs: 0 });
        context.args.prUrl = "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/42";
        context.step = async (key, producer, options) => {
            if (options?.volatile) return producer();
            if (!journal.has(key)) journal.set(key, structuredClone(await producer()));
            return structuredClone(journal.get(key));
        };
        return context;
    };
    const buildSnapshot = (id, failed = true) => snapshot({ provider: "ado", prBuilds: [{
        id, sourceHeadSha: "a".repeat(40), status: failed ? "completed" : "inProgress", result: failed ? "failed" : null,
        failedTasks: failed ? [{ name: "Checkout", excerpt: ["Connection reset by peer"] }] : [],
    }] });
    let reads = 0;
    const dependency = {
        exec: () => "", sleep: async () => {},
        retryFailedBuilds: () => { retries++; return true; },
    };
    const first = await runPrObserver(make(), {
        ...dependency, fetchPrSnapshot: () => ++reads === 1 ? buildSnapshot(77) : buildSnapshot(78, false),
    });
    assert.equal(first.status, "waiting-for-ci");
    const replay = await runPrObserver(make(), {
        ...dependency, fetchPrSnapshot: () => buildSnapshot(78),
    });
    assert.equal(retries, 1);
    assert.equal(replay.blockerKind, "ci-infrastructure");
    assert.equal(replay.repairAttempts, 0);
});

test("pending CI returns explicit continuation without spending classifier agents", async () => {
    const context = ctx();
    const logs = [];
    context.log = (text) => logs.push(text);
    const result = await runPrObserver(context, {
        exec: () => "", fetchPrSnapshot: () => snapshot({ statusCheckRollup: [{ name: "CI", state: "PENDING" }] }),
    });
    assert.equal(result.status, "waiting-for-ci");
    assert.equal(result.ci.status, "pending");
    assert.equal(context.calls.length, 0);
    assert.equal(result.continuation.args.prUrl, prUrl);
    const milestone = logs.map((text) => milestoneFromProgress({ kind: "log", text })).find((item) => item?.title === "PR observation");
    assert.equal(milestone.title, "PR observation");
    assert.equal(milestone.outcome, "deferred");
    assert.match(milestone.summary, /Waiting for CI results for the current PR head/);
    assert.match(milestone.summary, /Continue observing this PR with the remaining repair allowance/);
    assert.doesNotMatch(milestone.summary, /pending|not merge approval|success is not established/);
});

test("live CI handoff messages preserve queued versus running without spending agents or repeating each poll", async () => {
    for (const [state, expected] of [["QUEUED", "CI checks are queued."], ["IN_PROGRESS", "CI checks are running."], ["PENDING", "Waiting for CI results for the current PR head."]]) {
        const context = ctx([], { maxCiPollAttempts: 2, ciPollIntervalMs: 0 });
        const logs = [];
        context.log = (text) => logs.push(text);
        const result = await runPrObserver(context, {
            exec: () => "", sleep: async () => {},
            fetchPrSnapshot: () => snapshot({ statusCheckRollup: [{ name: "CI", status: state }] }),
        });
        const waits = logs.map((text) => milestoneFromProgress({ kind: "log", text })).filter((item) => item?.title === "Waiting for CI");
        assert.equal(waits.length, 1, "One durable wait update per head, not one per poll");
        assert.ok(waits[0].summary.startsWith(expected));
        assert.equal(result.summary, expected);
        assert.equal(result.ci.status, "pending");
        assert.equal(result.status, "waiting-for-ci");
        assert.equal(context.calls.length, 0);
    }
});

test("external blocker milestones name who can act and keep the actual CI diagnostic", async () => {
    const context = ctx();
    const logs = [];
    context.log = (text) => logs.push(text);
    const diagnostic = "Hosted image unavailable: provider unable to process this request during brownout.";
    const result = await runPrObserver(context, {
        exec: () => "",
        fetchPrSnapshot: () => snapshot({ prBuilds: [{ id: 7, status: "completed", result: "canceled", infrastructureIssues: [diagnostic] }] }),
    });
    const milestone = logs.map((text) => milestoneFromProgress({ kind: "log", text })).find((item) => item?.title === "PR observation");
    assert.match(milestone.summary, /CI provider or pipeline owner must resolve/);
    assert.equal(milestone.outcome, "blocked");
    assert.ok(result.evidence.includes(diagnostic));
    assert.equal(context.calls.length, 0);
});

test("pending CI never bypasses merge conflicts or actionable review feedback before or after a poll", async () => {
    for (const feedback of [
        { mergeStateStatus: "DIRTY" },
        { inlineReviewComments: [{ body: "Fix Foo.kt: missing cancellation handling", path: "Foo.kt", line: 12 }] },
    ]) {
        for (const afterPoll of [false, true]) {
            const context = ctx([{ verdict: "needs-owner-decision", repairKind: "none", summary: "Review the actionable blocker", evidence: [], actionableFindings: [] }],
                { maxCiPollAttempts: 2, ciPollIntervalMs: 0 });
            let reads = 0, sleeps = 0;
            const result = await runPrObserver(context, {
                exec: () => "", sleep: async () => { sleeps++; },
                fetchPrSnapshot: () => snapshot({
                    statusCheckRollup: [{ name: "CI", state: "PENDING" }],
                    ...(++reads > 1 || !afterPoll ? feedback : {}),
                }),
            });
            assert.equal(context.calls.length, 1, "The conflict/review classifier must run even though CI is pending");
            assert.equal(sleeps, afterPoll ? 1 : 0, "A repairable blocker must not consume the CI-only polling budget");
            assert.equal(result.status, "needs-owner-decision");
        }
    }
});

const classifiedWait = {
    verdict: "waiting-for-ci", repairKind: "none", summary: "Review feedback has no blocking findings; CI is still running.",
    evidence: ["Existing feedback was classified."], actionableFindings: [],
};
const reviewedWaitingSnapshot = () => snapshot({
    statusCheckRollup: [{ name: "CI", state: "PENDING" }, { name: "Advisory", state: "SUCCESS" }],
    prBuilds: [{ id: 91, status: "inProgress", sourceHeadSha: "a".repeat(40) }],
    inlineReviewComments: [{ id: 7, body: "Consider simplifying this optional helper.", path: "Foo.kt", line: 12 }],
    activeReviewThreads: [{ id: 8, status: "active", comments: [{ id: 9, content: "Optional style suggestion." }] }],
    reviews: [{ id: 10, author: { login: "reviewer" }, state: "COMMENTED", body: "Optional observations." }],
    comments: [{ id: 11, author: { login: "reviewer" }, body: "Existing nonblocking note." }],
    policyEvaluations: [{ evaluationId: 12, status: "queued", blocking: true, minimumApproverCount: 1 }],
});

test("unchanged classified feedback uses one observer across sixty CI waits, including the final bound", async () => {
    const context = ctx([structuredClone(classifiedWait)], { maxCiPollAttempts: 60, ciPollIntervalMs: 0 });
    let reads = 0, sleeps = 0;
    const result = await runPrObserver(context, {
        exec: () => "", sleep: async () => { sleeps++; },
        fetchPrSnapshot: () => {
            const value = reviewedWaitingSnapshot();
            value.collectedAt = ++reads;
            value.inlineReviewComments[0].updatedAt = String(reads);
            value.prBuilds[0].startTime = String(reads);
            if (reads % 2) value.statusCheckRollup.reverse();
            return value;
        },
    });
    assert.equal(context.calls.length, 1, "Already-classified feedback must not spend an agent on every CI wait");
    assert.equal(sleeps, 60);
    assert.equal(reads, 61);
    assert.equal(result.status, "waiting-for-ci");
    assert.equal(result.ci.status, "pending");
    assert.equal(result.summary, classifiedWait.summary);
    assert.equal(result.repairAttempts, 0);
    assert.equal(result.continuation.args.maxIterations, 2, "Waiting must not consume or reset the tactical repair allowance");
});

test("zero source-repair allowance is described as disabled repairs, not exhausted budget or failed CI", async () => {
    const context = ctx([{
        verdict: "repair", repairKind: "review-comment", summary: "A review finding needs source verification.",
        evidence: [], actionableFindings: ["Verify the unresolved review finding."],
    }]);
    context.args.maxIterations = 0;
    const logs = [];
    context.log = (value) => logs.push(value);
    const result = await runPrObserver(context, { exec: () => "", fetchPrSnapshot: reviewedWaitingSnapshot });
    assert.equal(result.status, "max-iterations-reached", "The raw protocol remains unchanged");
    assert.equal(result.repairAttempts, 0);
    assert.equal(context.calls.length, 1);
    assert.match(result.summary, /Source repairs are disabled for this run/);
    assert.doesNotMatch(result.summary, /budget exhausted|remaining failures/);
    const milestones = logs.map((value) => milestoneFromProgress({ kind: "log", text: value })).filter(Boolean);
    assert.ok(milestones.some((value) => /Source repairs are disabled for this run/.test(value.summary)));
    assert.ok(milestones.every((value) => !/repair allowance is used up/i.test(value.summary)));
});

test("reused waiting decisions are invalidated by edited feedback, policies, diagnostics and source identity", async () => {
    const changes = [
        ["inline body", (value) => { value.inlineReviewComments[0].body = "Fix cancellation before merging."; }],
        ["thread body", (value) => { value.activeReviewThreads[0].comments[0].content = "Fix a newly identified defect."; }],
        ["review body", (value) => { value.reviews[0].body = "New required review finding."; }],
        ["review state", (value) => { value.reviews[0].state = "CHANGES_REQUESTED"; }],
        ["top-level body", (value) => { value.comments[0].body = "New required source correction."; }],
        ["policy", (value) => { value.policyEvaluations[0].minimumApproverCount = 2; }],
        ["source head", (value) => { value.headRefOid = "b".repeat(40); }],
        ["merge head", (value) => { value.mergeHeadSha = "b".repeat(40); }],
        ["build binding", (value) => { value.prBuilds[0].sourceHeadSha = "b".repeat(40); }],
        ["CI description", (value) => { value.statusCheckRollup[0].description = "New provider diagnostic."; }],
        ["merge conflict", (value) => { value.mergeStateStatus = "DIRTY"; }],
        ["PR narrative", (value) => { value.body = "New owner instructions about the change."; }],
    ];
    for (const [label, change] of changes) {
        const context = ctx([structuredClone(classifiedWait), {
            verdict: "needs-owner-decision", repairKind: "none", summary: "New evidence needs an owner decision.",
            evidence: [], actionableFindings: [],
        }], { maxCiPollAttempts: 4, ciPollIntervalMs: 0 });
        context.args.maxIterations = 0;
        let reads = 0, sleeps = 0;
        const result = await runPrObserver(context, {
            exec: () => "", sleep: async () => { sleeps++; },
            fetchPrSnapshot: () => {
                const value = reviewedWaitingSnapshot();
                if (++reads >= 3) change(value);
                return value;
            },
        });
        assert.equal(context.calls.length, 2, label);
        assert.equal(sleeps, 2, label);
        assert.equal(result.status, "needs-owner-decision", label);
    }
});

test("new failed tasks interrupt a reused CI wait without another classifier or a repair-budget reset", async () => {
    const context = ctx([structuredClone(classifiedWait)], { maxCiPollAttempts: 5, ciPollIntervalMs: 0 });
    context.args.maxIterations = 0;
    let reads = 0;
    const result = await runPrObserver(context, {
        exec: () => "", sleep: async () => {},
        fetchPrSnapshot: () => {
            const value = reviewedWaitingSnapshot();
            if (++reads >= 3) {
                value.prBuilds[0].status = "completed";
                value.prBuilds[0].result = "failed";
                value.prBuilds[0].failedTasks = [{ name: "Unit tests", failedTests: [{ testName: "CancellationTest", errorMessage: "New exact-head regression" }] }];
                value.statusCheckRollup[0].state = "FAILURE";
            }
            return value;
        },
    });
    assert.equal(context.calls.length, 1);
    assert.equal(result.status, "max-iterations-reached");
    assert.equal(result.ci.status, "failed");
    assert.equal(result.repairAttempts, 0);
    assert.ok(result.actionableFindings.some((finding) => finding.includes("New exact-head regression")));
    assert.equal(result.continuation.args.maxIterations, 0);
});

test("CI completion replaces an earlier waiting classification with a fresh terminal decision", async () => {
    const context = ctx([structuredClone(classifiedWait), {
        verdict: "ready", repairKind: "none", summary: "Current CI and source checks passed.",
        evidence: ["Exact-head CI succeeded."], actionableFindings: [],
    }], { maxCiPollAttempts: 4, ciPollIntervalMs: 0 });
    let reads = 0;
    const result = await runPrObserver(context, {
        exec: () => "", sleep: async () => {},
        fetchPrSnapshot: () => {
            const value = reviewedWaitingSnapshot();
            if (++reads >= 3) {
                value.statusCheckRollup[0].state = "SUCCESS";
                value.prBuilds[0].status = "completed";
                value.prBuilds[0].result = "succeeded";
                value.policyEvaluations = [];
            }
            return value;
        },
    });
    assert.equal(context.calls.length, 2);
    assert.equal(result.status, "ready-for-human-merge");
    assert.equal(result.ci.status, "passed");
    assert.equal(result.ci.headSha, "a".repeat(40));
    assert.equal(result.humanMergeOnly, true);
});

test("a stale snapshot invalidates classified waiting and explicitly waits for fresh head evidence", async () => {
    const context = ctx([structuredClone(classifiedWait)], { maxCiPollAttempts: 3, ciPollIntervalMs: 0 });
    let reads = 0;
    const result = await runPrObserver(context, {
        exec: () => "", sleep: async () => {},
        fetchPrSnapshot: () => {
            const value = reviewedWaitingSnapshot();
            if (++reads >= 3) value.snapshotHeadChanged = true;
            return value;
        },
    });
    assert.equal(context.calls.length, 1);
    assert.equal(result.status, "waiting-for-ci");
    assert.match(result.summary, /PR head changed; waiting for fresh CI results/);
    assert.notEqual(result.summary, classifiedWait.summary);
    assert.notEqual(result.ci.status, "passed");
});

test("GitHub and GHE observers enrich readable Azure build diagnostics through the supplied executor", async () => {
    for (const url of [prUrl, "https://msft.ghe.com/security/ad-accounts-for-android/pull/42"]) {
        const parsed = parsePullRequestUrl(url);
        const calls = [];
        const exec = (command, args) => {
            calls.push([command, args]);
            if (command === "gh" && args[0] === "auth") return args[1] === "status" ? "Logged in to github.com account fixture (keyring)" : "fixture-token";
            if (command === "gh" && args[0] === "pr") return JSON.stringify(snapshot({
                statusCheckRollup: [{ name: "Android validation", conclusion: "FAILURE",
                    detailsUrl: "https://dev.azure.com/fixture/Engineering/_build/results?buildId=123" }],
            }));
            if (command === "gh" && args.includes("graphql")) return JSON.stringify({
                data: { repository: { pullRequest: { reviewThreads: { nodes: [{
                    id: "fixture-thread", isResolved: false, path: "Foo.kt", line: 12,
                    comments: { nodes: [{ databaseId: 7, body: "Fix Foo", author: { login: "reviewer" } }] },
                }] } } } },
            });
            if (command === "powershell") return JSON.stringify([{
                id: 123, definition: "Android validation", status: "completed", result: "failed",
                failedTasks: [{ name: "Compile", excerpt: ["Foo.kt:12: Unresolved reference: launchForResult"] }],
            }]);
            return "";
        };
        const observed = fetchPullRequestSnapshot(parsed, { exec });
        assert.equal(observed.inlineReviewComments?.[0]?.threadId, "fixture-thread");
        assert.match(observed.prBuilds?.[0]?.failedTasks?.[0]?.excerpt?.[0] || "", /Unresolved reference/);
        const context = ctx();
        context.args.prUrl = url;
        context.args.maxIterations = 0;
        const result = await runPrObserver(context, { exec });
        assert.equal(result.status, "max-iterations-reached", "Readable current failure diagnostics must reach the repair path");
        assert.notEqual(result.blockerKind, "ci-diagnostics");
        assert.ok(result.actionableFindings.some((finding) => finding.includes("Unresolved reference")));
        assert.ok(calls.some(([command]) => command === "powershell"));
    }
});

test("GitHub check enrichment executes its production PowerShell and retains unreadable diagnostics", {
    skip: process.platform !== "win32",
}, () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "github-ado-check-"));
    try {
        for (const failure of ["none", "timeline", "log"]) {
            const exec = (command, args) => {
                if (command === "gh" && args[0] === "auth") return "fixture-token";
                if (command === "gh" && args[0] === "pr") return JSON.stringify(snapshot({
                    statusCheckRollup: [{ name: "Android validation", conclusion: "FAILURE",
                        detailsUrl: "https://dev.azure.com/fixture/Engineering/_build/results?buildId=123" }],
                }));
                if (command === "gh" && args.includes("graphql")) return "{}";
                assert.equal(command, "powershell");
                const script = [
                    "$global:LASTEXITCODE = 0",
                    "function az { 'fixture-token' }",
                    "function Invoke-RestMethod { param($Uri,$Headers,$Method)",
                    `if ('${failure}' -eq 'timeline') { throw 'Fixture timeline denied' }`,
                    "if ($Uri -match '/timeline\\?') { return @{ records=@(@{type='Task';result='failed';name='Compile';issues=@();log=@{url='https://dev.azure.com/fixture/Engineering/_apis/build/builds/123/logs/1'}}) } }",
                    `if ('${failure}' -eq 'log') { throw 'Fixture log denied' }`,
                    "if ($Uri -match '/logs/1\\?') { return 'Foo.kt:12: Unresolved reference: launchForResult' }",
                    "throw 'Unexpected diagnostic request'",
                    "}",
                    args[args.indexOf("-Command") + 1],
                ].join("\n");
                const file = path.join(root, "fixture.ps1");
                writeFileSync(file, script);
                return execFileSync("powershell", ["-NoProfile", "-File", file], { encoding: "utf8", stdio: "pipe", timeout: 30_000 });
            };
            const observed = fetchPullRequestSnapshot(parsePullRequestUrl(prUrl), { exec });
            const build = observed.prBuilds?.[0];
            assert.ok(build, "The supplied executor must run Azure check enrichment");
            if (failure === "none") {
                assert.match(build.failedTasks[0].excerpt[0], /Unresolved reference/);
                assert.deepEqual(build.diagnosticErrors, []);
            } else {
                assert.match(build.diagnosticErrors[0], new RegExp(`Fixture ${failure} denied`));
            }
        }
    } finally { rmSync(root, { recursive: true, force: true }); }
});

test("observer continuations carry remaining repair budget and an explicit zero never resets it", async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "observer-budget-"));
    try {
        const context = ctx([{ verdict: "pass", summary: "Fixed source", evidence: [] }, passedReview], {
            workspaceRoot: root, pushRepairs: false,
        });
        let reads = 0;
        const failed = () => snapshot({ prBuilds: [{ id: 1, status: "completed", result: "failed", failedTasks: [{ name: "Compile", excerpt: ["Unresolved reference"] }] }] });
        const result = await runPrObserver(context, {
            exec: () => "",
            fetchPrSnapshot: () => ++reads === 1 ? failed() : snapshot({ statusCheckRollup: [{ name: "CI", state: "pending" }] }),
        });
        assert.equal(result.repairAttempts, 1);
        assert.equal(result.continuation.args.maxIterations, 1);
        const exhausted = ctx([], { workspaceRoot: root });
        exhausted.args.maxIterations = 0;
        const final = await runPrObserver(exhausted, { exec: () => "", fetchPrSnapshot: failed });
        assert.equal(final.status, "max-iterations-reached");
        assert.equal(final.repairAttempts, 0);
        assert.equal(final.continuation.args.maxIterations, 0);
        assert.equal(exhausted.calls.length, 0);
    } finally { rmSync(root, { recursive: true, force: true }); }
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
        const invalid = path.join(root, "jdk8");
        mkdirSync(invalid);
        writeFileSync(path.join(invalid, "release"), 'JAVA_VERSION="1.8.0"\n');
        const noSelection = ctx([{ verdict: "pass", summary: "Source fixed", evidence: [] }, passedReview], {
            workspaceRoot: root, pushRepairs: false,
        });
        let moreReads = 0;
        await runPrObserver(noSelection, {
            environment: { JAVA_HOME: invalid }, exec: () => "",
            fetchPrSnapshot: () => ++moreReads === 1
                ? snapshot({ prBuilds: [{ id: 1, status: "completed", result: "failed", failedTasks: [{ name: "Compile", excerpt: ["Unresolved reference: Foo"] }] }] })
                : snapshot(),
        });
        assert.ok(!noSelection.calls[0].prompt.includes(`Use JAVA_HOME ${invalid}`));
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
                if (command === "git" && ["rev-parse", "write-tree", "merge-base"].includes(args[0])) return "a".repeat(40);
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
