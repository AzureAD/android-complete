// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, rmdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createWorkflowCompletionDelivery, createWorkflowStatusTool, formatWorkflowCompletion } from "./completion.mjs";

const pr = "https://github.com/AzureAD/example/pull/42";
const adoPr = "https://dev.azure.com/IdentityDivision/Engineering/_git/authenticator/pullrequest/43";

function prLinkUrls(markdown) {
    return [...markdown.matchAll(/^PR(?: link in partial snapshot \(publication\/verification not confirmed\))?: (https:\/\/\S+)$/gm)]
        .map(([, value]) => new URL(value).href);
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((ok, no) => { resolve = ok; reject = no; });
    return { promise, resolve, reject };
}

function completed(overrides = {}) {
    return {
        runId: "run-1",
        attempt: 1,
        status: "completed",
        result: {
            recommendation: "GO to PR Review",
            pullRequests: [{ url: pr }],
            observations: [{ prUrl: pr, status: "ready-for-human-merge", ci: { status: "passed", headSha: "abc123" }, summary: "Required CI passed." }],
        },
        ...overrides,
    };
}

class FakeSession {
    sessionId = "owning-session";
    handlers = new Set();
    runs = new Map();
    waits = new Map();
    messages = [];
    errors = [];
    calls = [];
    progress = [];
    readHook;
    sendHook;
    listHook;

    constructor(runs = []) {
        for (const [run, name = "code-feature"] of runs) this.set(run, name);
        const forbidden = () => { throw new Error("Completion delivery must not run/resume/pause workflows"); };
        this.workflow = {
            run: forbidden,
            resume: forbidden,
            pause: forbidden,
            listRuns: async (options) => {
                this.calls.push(["listRuns", options]);
                return this.listHook?.(options) ?? {
                    runs: [...this.runs.values()].map(({ run, name }) => ({
                        runId: run.runId, workflowName: name, status: run.status,
                    })),
                    oldestSeq: 1,
                    omittedOlder: 0,
                };
            },
            getRun: async (id) => {
                this.calls.push(["getRun", id]);
                await this.readHook?.(id);
                return structuredClone(this.runs.get(id).run);
            },
            getRunDetail: async (id) => {
                this.calls.push(["getRunDetail", id]);
                return { workflowName: this.runs.get(id).name };
            },
            getRunProgress: async (id, options) => {
                this.calls.push(["getRunProgress", id, options]);
                return { records: this.progress };
            },
            waitForRun: (id, { signal }) => {
                this.calls.push(["waitForRun", id]);
                const wait = deferred();
                this.waits.set(id, wait);
                signal.addEventListener("abort", () => wait.reject(new Error("Aborted")), { once: true });
                return wait.promise;
            },
        };
    }

    set(run, name = this.runs.get(run.runId)?.name ?? "code-feature") {
        this.runs.set(run.runId, { run, name });
    }

    emit(type, data = {}, agentId) {
        for (const handler of this.handlers) handler({ type, data, agentId });
    }

    on(handler) {
        this.handlers.add(handler);
        return () => this.handlers.delete(handler);
    }

    async send(message) {
        await this.sendHook?.(message);
        this.messages.push(message);
        return `message-${this.messages.length}`;
    }

    async log(message) {
        this.errors.push(message);
    }
}

function fixture(t, runs = [], options = {}) {
    const root = path.join(process.cwd(), ".completion-test-fixtures", randomUUID());
    mkdirSync(root, { recursive: true });
    const session = new FakeSession(runs);
    const errors = [];
    const deliveries = [];
    function install(overrides = {}, owningSession = session) {
        const delivery = createWorkflowCompletionDelivery(owningSession, {
            workspaceRoot: root,
            reportError: (error) => errors.push(error),
            ...options,
            ...overrides,
        });
        deliveries.push(delivery);
        return delivery;
    }
    t.after(async () => {
        for (const delivery of deliveries) delivery.dispose();
        await Promise.all(deliveries.map((delivery) => delivery.flush()));
        rmSync(root, { recursive: true, force: true });
        // Other tests may still own sibling fixture directories.
        try { rmdirSync(path.dirname(root)); } catch (error) {
            if (!["ENOTEMPTY", "ENOENT", "EEXIST"].includes(error.code)) throw error;
        }
    });
    function stateFile() {
        const directory = path.join(root, ".github", "local-context", "code-feature", "completion");
        return path.join(directory, readdirSync(directory).find((file) => file.endsWith(".json")));
    }
    return { session, errors, install, stateFile };
}

test("PR link assertions compare complete parsed URLs, not a trusted URL embedded in another URL", () => {
    for (const trustedUrl of [pr, adoPr]) {
        const untrustedUrl = new URL(`https://untrusted.example/?next=${trustedUrl}`).href;
        const links = prLinkUrls(`PR: ${untrustedUrl}`);
        assert.deepEqual(links, [untrustedUrl]);
        assert.notEqual(links[0], trustedUrl);
        assert.deepEqual(prLinkUrls(`Result notes: see ${trustedUrl}`), []);
    }
});

test("production PR URL parsing rejects invalid protocols, credentials and nested-query PR strings", () => {
    const invalidUrls = [
        "http://github.com/AzureAD/example/pull/42",
        "https://user:password@github.com/AzureAD/example/pull/42",
        `https://untrusted.example/?next=${pr}`,
        `https://untrusted.example/?next=${adoPr}`,
        "https://github.com/AzureAD/example/not-a-pull/42",
        "javascript:alert(1)",
        "not-a-url",
    ];
    const prompt = formatWorkflowCompletion("code-feature", completed({
        result: { pullRequests: invalidUrls.map((url) => ({ url })), observations: [] },
    }));
    assert.deepEqual(prLinkUrls(prompt), []);
    assert.match(prompt, /no published URL recorded/);
});

test("startup catch-up delivers durable completion to the owning session with honest PR/verification details", async (t) => {
    const { session, install, errors, stateFile } = fixture(t, [[completed()]]);
    const delivery = install();
    await delivery.ready;
    assert.equal(session.messages.length, 1);
    const message = session.messages[0];
    assert.equal(message.mode, "immediate");
    assert.equal(message.source, "system");
    assert.equal(message.agentMode, undefined, "Preserve the caller's current mode");
    assert.match(message.prompt, /Workflow code-feature: completed/);
    assert.match(message.prompt, /attempt 1/);
    assert.deepEqual(prLinkUrls(message.prompt), [pr]);
    assert.match(message.prompt, /observer reports ready-for-human-merge/);
    assert.match(message.prompt, /Human review and merge approval are still required/);
    assert.match(message.prompt, /\[workflow-outcome:run-1:[a-f0-9]{64}\]/);
    assert.equal(errors.length, 0);
    const state = JSON.parse(readFileSync(stateFile(), "utf8"));
    assert.equal(state.runs["run-1"].delivered.length, 1);
    assert.equal(state.runs["run-1"].pending, undefined);
});

test("completed code-feature does not claim CI success or a continuing observer for a pending/missing handoff", async (t) => {
    const run = completed();
    run.result.pullRequests.push({ url: adoPr });
    run.result.observations.push({ prUrl: adoPr, status: "waiting-for-ci", summary: "Observer stopped after timeout." });
    run.result.implementation = { targets: [{ pr: { testPlan: "Local Gradle deferred to CI." } }] };
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    const prompt = session.messages[0].prompt;
    assert.deepEqual(prLinkUrls(prompt), [pr, adoPr]);
    assert.match(prompt, /Observer: waiting-for-ci/);
    assert.match(prompt, /Verification is not confirmed ready for every PR/);
    assert.match(prompt, /No continuing observer or automatic merge is implied/);
    assert.match(prompt, /Local verification was deferred or incomplete/);
    assert.doesNotMatch(prompt, /Verification: observer reports ready-for-human-merge/);
});

test("completed NO-GO and missing results remain non-successful deliverables", async (t) => {
    const { session, install } = fixture(t, [
        [completed({ result: { recommendation: "NO-GO", reasons: ["Unit test assertion failed"], questions: ["Which requirement applies?"] } })],
        [completed({ runId: "run-2", result: undefined })],
    ]);
    await install().ready;
    assert.equal(session.messages.length, 2);
    const prompt = session.messages.find((message) => message.prompt.includes("run run-1")).prompt;
    assert.match(prompt, /Recommendation: NO-GO/);
    assert.match(prompt, /Unit test assertion failed/);
    assert.match(prompt, /Needs input: Which requirement applies/);
    for (const message of session.messages) {
        assert.match(message.prompt, /no published URL recorded/);
        assert.match(message.prompt, /CI is unverified here/);
    }
});

for (const status of ["paused", "error", "halted", "cancelled"]) {
    test(`${status} reports an actual settled attempt with recovery guidance, never success`, async (t) => {
        const run = completed({
            status,
            result: { summary: "This stale result must not be used." },
            error: status === "error" ? "Provider connection lost" : undefined,
            reason: status === "halted" ? "Host interrupted" : undefined,
            pauseInfo: status === "paused" ? { type: "checkpoint", key: "choose-jdk" } : undefined,
            snapshot: { journal: { "pull-requests": [{ url: adoPr }] } },
        });
        const { session, install } = fixture(t, [[run]]);
        session.progress = [
            { attempt: 0, text: "Stale progress must not be used" },
            { attempt: 1, text: "Requires an approved compatible JDK before resume." },
        ];
        await install().ready;
        const prompt = session.messages[0].prompt;
        assert.match(prompt, new RegExp(`Workflow code-feature: ${status}`));
        assert.match(prompt, /Verification: incomplete\/unconfirmed/);
        assert.match(prompt, /publication\/verification not confirmed/);
        assert.deepEqual(prLinkUrls(prompt), [adoPr]);
        assert.match(prompt, /Requires an approved compatible JDK/);
        assert.doesNotMatch(prompt, /stale result|Stale progress/);
        assert.match(prompt, status === "paused" ? /resume this same run run-1/ : /establish resumability/);
        if (status === "paused") assert.match(prompt, /Paused checkpoint: choose-jdk/);
    });
}

test("limit failures ask approval to raise only the reached ceiling before same-run resume", async (t) => {
    const run = completed({
        status: "error",
        failure: { type: "workflow_limit_reached", kind: "timeoutSeconds", value: 100 },
    });
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    assert.match(session.messages[0].prompt, /request approval to raise only the timeoutSeconds ceiling, then resume run-1/);
});

test("pr-observer completion includes its independent result and PR link", async (t) => {
    const run = completed({ result: { status: "needs-owner-decision", prUrl: adoPr, summary: "Review requests a scope decision." } });
    const { session, install } = fixture(t, [[run, "pr-observer"]]);
    await install().ready;
    assert.match(session.messages[0].prompt, /Workflow pr-observer: completed/);
    assert.match(session.messages[0].prompt, /Observer: needs-owner-decision/);
    assert.deepEqual(prLinkUrls(session.messages[0].prompt), [adoPr]);
    assert.match(session.messages[0].prompt, /Review requests a scope decision/);
});

test("no parent idle or body return can imply completion; SDK wait settlement recovers dropped events", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install();
    await delivery.ready;
    session.emit("session.idle");
    await delivery.flush();
    assert.equal(session.messages.length, 0);
    assert.equal(session.waits.size, 1);
    session.set(completed());
    // Models waitForRun's documented durable reread fallback, with no lifecycle events.
    session.waits.get("run-1").resolve(completed());
    await new Promise((resolve) => setImmediate(resolve));
    await delivery.flush();
    assert.equal(session.messages.length, 1);
});

test("body-admission tracking covers a lost workflow start/listing event", async (t) => {
    const { session, install } = fixture(t);
    const delivery = install();
    await delivery.ready;
    session.set(completed({ status: "running" }));
    await delivery.track("run-1", "code-feature");
    assert.equal(session.waits.size, 1);
    session.set(completed());
    session.waits.get("run-1").resolve(completed());
    await new Promise((resolve) => setImmediate(resolve));
    await delivery.flush();
    assert.equal(session.messages.length, 1);
});

test("duplicate/concurrent lifecycle events serialize sends and dedupe by attempt/result rather than revision", async (t) => {
    const { session, install } = fixture(t);
    const delivery = install();
    await delivery.ready;
    const sending = deferred();
    const admitted = deferred();
    session.sendHook = async () => { sending.resolve(); await admitted.promise; };
    session.set(completed());
    session.emit("workflow.run_started", { runId: "run-1", workflowName: "code-feature", attempt: 1 });
    await sending.promise;
    for (let revision = 1; revision <= 20; revision++) {
        session.emit("workflow.run_updated", { runId: "run-1", revision });
        session.emit("workflow.run_settled", { runId: "run-1", status: "completed" });
    }
    admitted.resolve();
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    session.set(completed({ result: { observations: completed().result.observations, pullRequests: [{ url: pr }], recommendation: "GO to PR Review" } }));
    session.emit("workflow.run_updated", { runId: "run-1", revision: 99 });
    await delivery.flush();
    assert.equal(session.messages.length, 1, "Object key order does not change the result identity");
    session.set(completed({ result: { recommendation: "NO-GO", reasons: ["Corrected durable result"] } }));
    session.emit("workflow.run_updated", { runId: "run-1", revision: 100 });
    await delivery.flush();
    assert.equal(session.messages.length, 2, "A changed durable result is delivered, not hidden by run-ID-only dedupe");
});

test("paused/resumed attempts under one run ID each deliver once, including identical pause checkpoints", async (t) => {
    const paused = completed({ status: "paused", result: undefined, pauseInfo: { type: "checkpoint", key: "needs-help" } });
    const { session, install } = fixture(t, [[paused]]);
    const delivery = install();
    await delivery.ready;
    session.set({ ...paused, attempt: 2 });
    session.emit("workflow.run_started", { runId: "run-1", workflowName: "code-feature", attempt: 2 });
    await delivery.flush();
    session.set(completed({ attempt: 3 }));
    session.emit("workflow.run_settled", { runId: "run-1", status: "completed" });
    await delivery.flush();
    assert.equal(session.messages.length, 3);
    assert.match(session.messages[1].prompt, /attempt 2/);
    assert.match(session.messages[2].prompt, /attempt 3/);
});

test("restart catches a missed settlement and acknowledged results are not replayed again", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "running" })]]);
    const first = install();
    await first.ready;
    first.dispose();
    session.set(completed());
    const second = install();
    await second.ready;
    assert.equal(session.messages.length, 1);
    second.dispose();
    const third = install();
    await third.ready;
    assert.equal(session.messages.length, 1);
});

test("send failure is visible and durable; restart retries the same outcome and marker", async (t) => {
    const { session, install, stateFile, errors } = fixture(t, [[completed()]]);
    let failedPrompt;
    session.sendHook = (message) => { failedPrompt = message.prompt; throw new Error("Owner temporarily unavailable"); };
    const first = install();
    await first.ready;
    assert.equal(session.messages.length, 0);
    assert.match(errors[0].message, /run-1 outcome was not acknowledged/);
    const pending = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"];
    assert.equal(pending.pending.prompt, failedPrompt);
    assert.deepEqual(pending.delivered, []);
    assert.match(pending.lastError, /Owner temporarily unavailable/);
    first.dispose();
    session.sendHook = undefined;
    const second = install();
    await second.ready;
    assert.equal(session.messages.length, 1);
    assert.equal(session.messages[0].prompt, failedPrompt);
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].pending, undefined);
});

test("delivery failures retry automatically without workflow polling or parent idleness", async (t) => {
    const { session, install, errors } = fixture(t, [[completed()]], { retryDelayMs: 5 });
    const retried = deferred();
    let attempts = 0;
    session.sendHook = () => {
        if (++attempts === 1) throw new Error("Transient send failure");
        retried.resolve();
    };
    const delivery = install();
    await delivery.ready;
    const timeout = setTimeout(() => retried.reject(new Error("Retry did not happen")), 2000);
    try { await retried.promise; } finally { clearTimeout(timeout); }
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.equal(errors.length, 1);
    assert.equal(session.calls.filter(([method]) => method === "waitForRun").length, 0);
});

test("read failures retry from durable runs without falsely announcing an outcome", async (t) => {
    const { session, install, errors } = fixture(t, [[completed()]]);
    session.readHook = () => { throw new Error("Durable read failed"); };
    const delivery = install();
    await delivery.ready;
    assert.equal(session.messages.length, 0);
    assert.ok(errors.some((error) => /Durable read failed/.test(error.message)));
    session.readHook = undefined;
    await delivery.reconcile();
    assert.equal(session.messages.length, 1);
});

test("an outbox write failure never marks or sends a completion and is retriable", async (t) => {
    const { session, install, errors } = fixture(t, [[completed()]]);
    let state = { version: 1, sessionId: session.sessionId, runs: {} };
    let writable = false;
    const delivery = install({ store: {
        load: () => structuredClone(state),
        save: (next) => {
            if (!writable) throw new Error("Disk unavailable");
            state = structuredClone(next);
        },
    } });
    await delivery.ready;
    assert.equal(session.messages.length, 0);
    assert.equal(state.runs["run-1"], undefined);
    assert.ok(errors.some((error) => /Disk unavailable/.test(error.message)));
    writable = true;
    await delivery.reconcile();
    assert.equal(session.messages.length, 1);
    assert.equal(state.runs["run-1"].delivered.length, 1);
});

test("old pending pauses are replaced by the latest durable completed outcome after a restart", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "paused" })]]);
    session.sendHook = () => { throw new Error("Offline"); };
    const first = install();
    await first.ready;
    first.dispose();
    session.set(completed({ attempt: 2 }));
    session.sendHook = undefined;
    await install().ready;
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /completed.*attempt 2/);
    assert.doesNotMatch(session.messages[0].prompt, /resume this same run/);
});

test("catch-up pages older runs, filters unrelated workflows/subagents, and remains session scoped", async (t) => {
    const { session, install } = fixture(t, [
        [completed({ runId: "old-run" })],
        [completed({ runId: "new-run" })],
        [completed({ runId: "other-run" }), "another-workflow"],
    ]);
    session.listHook = ({ beforeSeq }) => beforeSeq === undefined
        ? { runs: [{ runId: "new-run", workflowName: "code-feature" }, { runId: "other-run", workflowName: "another-workflow" }], omittedOlder: 1, oldestSeq: 10 }
        : { runs: [{ runId: "old-run", workflowName: "code-feature" }], omittedOlder: 0, oldestSeq: 1 };
    const delivery = install();
    await delivery.ready;
    assert.equal(session.messages.length, 2);
    assert.ok(session.calls.some(([method, options]) => method === "listRuns" && options.beforeSeq === 10));
    session.set(completed({ runId: "child-run" }));
    session.emit("workflow.run_settled", { runId: "child-run" }, "child-agent");
    await delivery.flush();
    assert.equal(session.messages.length, 2);
    delivery.dispose();
    const differentOwner = new FakeSession();
    differentOwner.sessionId = "different-owner";
    await install({}, differentOwner).ready;
    assert.equal(differentOwner.messages.length, 0, "A different owner must not replay old runs or pending results from this owner's outbox");
    assert.equal(session.messages.length, 2, "All sends stay on the original owning session");
});

test("malformed persisted state is rejected rather than overwritten or silently redelivered", async (t) => {
    const { install, stateFile } = fixture(t, [[completed()]]);
    const first = install();
    await first.ready;
    first.dispose();
    writeFileSync(stateFile(), "{broken JSON", "utf8");
    assert.throws(() => install(), /Cannot read completion delivery state/);
});

test("deliveryStatus, verification receipts, observer continuation and publication errors are preserved without inferring readiness", async (t) => {
    const run = completed();
    run.result.deliveryStatus = "ci-failed";
    run.result.recommendation = "ci-failed";
    run.result.verification = [{
        targetId: "common",
        local: { status: "deferred", commands: ["not a successful receipt"], evidence: ["Build hit the local time limit."] },
        ci: { status: "failed", headSha: "abc123", evidence: ["Unit test failure on this head."] },
    }];
    run.result.observations[0] = {
        prUrl: pr,
        status: "waiting-for-ci",
        ci: { status: "failed", headSha: "abc123" },
        continuation: { workflow: "pr-observer", args: { prUrl: pr } },
        statusPublication: { status: "failed", error: "PR edit rejected" },
    };
    run.result.nextAction = "Resolve the explicit remaining failure; do not merge.";
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    const prompt = session.messages[0].prompt;
    assert.match(prompt, /Delivery status: ci-failed/);
    assert.match(prompt, /Verification common: local deferred; CI failed; head abc123/);
    assert.match(prompt, /Observed CI: failed; head abc123/);
    assert.match(prompt, /Build hit the local time limit/);
    assert.match(prompt, /PR status publication failed: PR edit rejected/);
    assert.match(prompt, /The PR body may be stale/);
    assert.match(prompt, /Observer handoff available: pr-observer/);
    assert.match(prompt, /Recorded continuation input is not a running workflow/);
    assert.match(prompt, /do not merge/);
    assert.doesNotMatch(prompt, /Verification: observer reports ready-for-human-merge/);
});

test("legacy observer status without explicit CI evidence cannot claim current-head success", async (t) => {
    const run = completed();
    delete run.result.observations[0].ci;
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    assert.match(session.messages[0].prompt, /Observed CI: not-observed; head unknown/);
    assert.doesNotMatch(session.messages[0].prompt, /Verification: observer reports ready-for-human-merge/);
});

test("failed PR status publication prevents a readiness claim even when CI passed", async (t) => {
    const run = completed();
    run.result.observations[0].statusPublication = { status: "failed", error: "Provider unavailable" };
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    assert.match(session.messages[0].prompt, /PR status publication failed/);
    assert.doesNotMatch(session.messages[0].prompt, /Verification: observer reports ready-for-human-merge/);
});

test("progress read failure is reported and the delivered recovery message explicitly says progress is unavailable", async (t) => {
    const { session, errors, install } = fixture(t, [[completed({ status: "paused" })]]);
    session.workflow.getRunProgress = async () => { throw new Error("Progress storage unavailable"); };
    await install().ready;
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /Progress unavailable; inspect this run/);
    assert.ok(errors.some((error) => /Progress storage unavailable/.test(error.message)));
});

test("a crash-window acknowledgement failure may replay the same stable marker, never falsely mark it delivered", async (t) => {
    const { session, errors, install } = fixture(t, [[completed()]]);
    let state = { version: 1, sessionId: session.sessionId, runs: {} };
    let writes = 0;
    const delivery = install({ store: {
        load: () => structuredClone(state),
        save: (next) => {
            if (++writes === 2) throw new Error("Acknowledgement write failed after admission");
            state = structuredClone(next);
        },
    } });
    await delivery.ready;
    assert.equal(session.messages.length, 1);
    assert.deepEqual(state.runs["run-1"].delivered, []);
    assert.ok(state.runs["run-1"].pending);
    assert.ok(errors.some((error) => /Acknowledgement write failed/.test(error.message)));
    await delivery.reconcile();
    assert.equal(session.messages.length, 2);
    assert.equal(session.messages[0].prompt, session.messages[1].prompt);
    assert.equal(state.runs["run-1"].delivered.length, 1);
});

test("missing required SDK observation API is an explicit startup error, not invented functionality", async (t) => {
    const { session, install } = fixture(t);
    delete session.workflow.waitForRun;
    assert.throws(() => install(), /SDK lacks session.workflow.waitForRun/);
});

test("read-only status tool exposes current phase and latest progress while a workflow is still running", async () => {
    const session = new FakeSession();
    const calls = [];
    session.workflow.getRunDetail = async (id) => {
        calls.push(["detail", id]);
        return {
            runId: id,
            workflowName: "code-feature",
            status: "running",
            revision: 23,
            observedAt: 123456,
            currentPhase: { title: "Implement" },
            canResume: false,
            agents: [{ agentId: "builder-1", status: "running", label: "Implementation" }],
        };
    };
    session.workflow.getRunProgress = async (id, options) => {
        calls.push(["progress", id, options]);
        return { records: [{ attempt: 1, text: "Starting implementation." }], revision: 24 };
    };
    let sessionReads = 0;
    const tool = createWorkflowStatusTool(() => { sessionReads++; return session; });
    assert.equal(sessionReads, 0, "Registration must not access the not-yet-joined session");
    assert.equal(tool.skipPermission, true);
    const result = JSON.parse(await tool.handler({ runId: "run-active", progressLimit: 5 }));
    assert.equal(result.status, "running");
    assert.equal(result.currentPhase.title, "Implement");
    assert.equal(result.progress.records[0].text, "Starting implementation.");
    assert.equal(result.agents[0].label, "Implementation");
    assert.equal(result.canResume, false);
    assert.equal(result.lastObservedPhase, null, "A reported current phase must not be replaced by historical progress");
    assert.equal(result.consumed, null, "Missing accounting must not be fabricated as zero usage");
    assert.equal(result.liveAgentCount, null);
    assert.equal(result.totalSpawnedAgentCount, null);
    assert.match(result.note, /not an atomic snapshot/);
    assert.deepEqual(calls, [["detail", "run-active"], ["progress", "run-active", { limit: 5 }]]);
    assert.equal(session.messages.length, 0);
});

test("SDK-shaped running detail exposes raw usage/direct counts and a historical phase fallback from bounded progress", async () => {
    const session = new FakeSession();
    const calls = [];
    const progress = {
        records: [
            { seq: 10, attempt: 1, phaseId: "p0", recordedAt: 1000, kind: "phase", text: "Gather evidence" },
            { seq: 11, attempt: 1, phaseId: "p0", recordedAt: 1100, kind: "log", text: "Starting code-evidence." },
            { seq: 12, attempt: 1, phaseId: null, recordedAt: 1200, kind: "log", text: "Status snapshot persisted." },
        ],
        oldestSeq: 10,
        newestSeq: 12,
        hasMoreOlder: true,
        hasMoreNewer: false,
        revision: 24,
    };
    const detail = {
        runId: "run-active",
        workflowName: "code-feature",
        description: "Implementation workflow",
        status: "running",
        revision: 23,
        createdAt: 100,
        startedAt: 200,
        updatedAt: 1200,
        completedAt: null,
        currentPhase: null,
        declaredPhaseCount: 3,
        liveAgentCount: 1,
        totalSpawnedAgentCount: 4,
        consumed: { activeMs: 98000, subagents: 7, nanoAiu: 123456789 },
        declaredLimits: {},
        approved: null,
        observedAt: 1300,
        activeSegmentStartedAt: 200,
        terminal: null,
        canResume: false,
        phases: [],
        agents: [{
            agentId: "evidence-1",
            toolCallId: "tool-1",
            runId: "run-active",
            phaseId: "p0",
            label: "Evidence",
            agentType: "code-evidence",
            status: "running",
            activeMs: 1000,
        }],
        progress,
    };
    session.workflow.getRunDetail = async (id) => { calls.push(["detail", id]); return detail; };
    session.workflow.getRunProgress = async (id, options) => { calls.push(["progress", id, options]); return progress; };
    session.workflow.getRun = async () => { throw new Error("Running envelopes have no accounting and must not be read for this status"); };
    const result = JSON.parse(await createWorkflowStatusTool(() => session).handler({ runId: "run-active", progressLimit: 3 }));
    assert.equal(result.status, "running");
    assert.equal(result.currentPhase, null);
    assert.deepEqual(result.consumed, detail.consumed);
    assert.equal(result.liveAgentCount, 1);
    assert.equal(result.totalSpawnedAgentCount, 4);
    assert.deepEqual(result.lastObservedPhase, {
        id: "p0",
        title: "Gather evidence",
        source: "durable-progress",
        isLive: false,
        seq: 11,
        attempt: 1,
        recordedAt: 1100,
    });
    assert.equal(result.deliveryStatus, null);
    assert.equal(result.settledSummary, null);
    assert.match(result.note, /nano-AIU \(not converted credits\)/);
    assert.match(result.note, /historical within the returned progress window, not a live\/current phase/);
    assert.deepEqual(calls, [["detail", "run-active"], ["progress", "run-active", { limit: 3 }]]);
    assert.equal(session.messages.length, 0);
});

test("historical phase fallback uses phase metadata without guessing from ordinary log prose and stays null without phase evidence", async () => {
    const session = new FakeSession();
    const detail = {
        runId: "run-active", workflowName: "code-feature", status: "running", currentPhase: null,
        phases: [{ id: "p0", title: "Gather evidence" }],
    };
    let records = [{ seq: 11, attempt: 1, phaseId: "p0", recordedAt: 1100, kind: "log", text: "Some ordinary log text" }];
    session.workflow.getRunDetail = async () => detail;
    session.workflow.getRunProgress = async () => ({ records });
    const tool = createWorkflowStatusTool(() => session);
    let result = JSON.parse(await tool.handler({ runId: "run-active" }));
    assert.equal(result.lastObservedPhase.title, "Gather evidence");
    detail.phases = [];
    result = JSON.parse(await tool.handler({ runId: "run-active" }));
    assert.equal(result.lastObservedPhase.id, "p0");
    assert.equal(result.lastObservedPhase.title, null, "Ordinary log prose is not a phase title");
    records = [{ seq: 12, attempt: 1, phaseId: null, recordedAt: 1200, kind: "log", text: "No phase recorded." }];
    result = JSON.parse(await tool.handler({ runId: "run-active" }));
    assert.equal(result.lastObservedPhase, null);
    assert.equal(result.currentPhase, null);
    assert.equal(session.messages.length, 0);
});

test("status tool validates bounds and keeps provider errors visible without running workflow controls", async () => {
    const session = new FakeSession([[completed(), "another-workflow"]]);
    const tool = createWorkflowStatusTool(() => session);
    await assert.rejects(() => tool.handler({ runId: "" }), /runId is required/);
    for (const limit of [0, 51, 1.5, "5"]) {
        await assert.rejects(() => tool.handler({ runId: "run-1", progressLimit: limit }), /progressLimit/);
    }
    await assert.rejects(() => tool.handler({ runId: "run-1" }), /only supports code-feature and pr-observer/);
    session.workflow.getRunDetail = async () => { throw new Error("Run is not owned by this session"); };
    await assert.rejects(() => tool.handler({ runId: "other-session-run" }), /not owned by this session/);
    assert.equal(session.messages.length, 0);
});

for (const deliveryStatus of ["awaiting-human-review", "ci-failed"]) {
    test(`status tool and completion notice expose the exact ${deliveryStatus} delivery outcome`, async (t) => {
        const run = completed();
        run.result.deliveryStatus = deliveryStatus;
        run.result.recommendation = deliveryStatus;
        run.result.verification = [{
            targetId: "common",
            local: { status: "deferred", evidence: ["Local execution deferred to CI."] },
            ci: { status: deliveryStatus === "ci-failed" ? "failed" : "passed", headSha: "abc123" },
        }];
        const { session, install } = fixture(t, [[run]]);
        session.workflow.getRunDetail = async (id) => ({
            runId: id, workflowName: "code-feature", status: "completed", currentPhase: null, canResume: false,
        });
        await install().ready;
        const result = JSON.parse(await createWorkflowStatusTool(() => session).handler({ runId: run.runId }));
        assert.equal(result.status, "completed");
        assert.equal(result.deliveryStatus, deliveryStatus);
        assert.equal(result.recommendation, deliveryStatus);
        assert.equal(result.verification[0].ci.status, run.result.verification[0].ci.status);
        assert.match(result.settledSummary, new RegExp(`Delivery status: ${deliveryStatus}`));
        assert.match(session.messages[0].prompt, new RegExp(`Delivery status: ${deliveryStatus}`));
        assert.doesNotMatch(result.settledSummary, /Verification: observer reports ready-for-human-merge/);
    });
}
