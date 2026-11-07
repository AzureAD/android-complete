// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, rmdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createWorkflowCompletionDelivery, createWorkflowStatusTool, formatWorkflowCompletion } from "./completion.mjs";
import { formatWorkflowMilestone, humanInputRequestFromProgress, milestoneFromProgress, recordHumanInputRequest, recordMilestone } from "./milestones.mjs";
import { deliveryStatusSummary } from "./status.mjs";

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

test("milestone presentation uses the actual summary, not a misleading completed label for dispatch", () => {
    const milestone = { id: "builder-start", title: "Implementation started", outcome: "completed", summary: "Implementation in progress." };
    const prompt = formatWorkflowMilestone("code-feature", "run-1", milestone, 1000);
    assert.match(prompt, /Implementation in progress/);
    assert.doesNotMatch(prompt, /Implementation started — completed|Workflow code-feature:/);
    assert.match(prompt, /not proof that the next stage started or that work is still active/);
    assert.equal(milestone.outcome, "completed", "Presentation must not rewrite the durable receipt schema");
});

test("terminal failures are not softened to deferred verification or a successful workflow", () => {
    const run = completed({
        result: {
            recommendation: "NO-GO", deliveryStatus: "ci-failed", pullRequests: [{ url: pr }],
            verification: [{ targetId: "common", local: { status: "failed", evidence: ["FooTest assertion failed."] }, ci: { status: "failed", headSha: "abc123" } }],
            observations: [{ prUrl: pr, status: "max-iterations-reached", ci: { status: "failed", headSha: "abc123" }, summary: "FooTest failed twice.", actionableFindings: ["FooTest.kt:12 expected tenant A but received tenant B"] }],
        },
    });
    const prompt = formatWorkflowCompletion("code-feature", run);
    assert.match(prompt, /Local verification failed\. FooTest assertion failed/);
    assert.match(prompt, /CI failed for the current PR head/);
    assert.match(prompt, /repair allowance is used up/);
    assert.match(prompt, /Remaining finding: FooTest.kt:12 expected tenant A but received tenant B/);
    assert.doesNotMatch(prompt, /Local verification is incomplete|CI and observed PR checks passed|Delivery status:|Recommendation:/);
});

test("terminal messages distinguish unpublished packets, human approvals and external owner blockers", () => {
    const packet = formatWorkflowCompletion("code-feature", completed({
        result: { deliveryStatus: "prepared-for-review", pullRequests: [{ url: null }], observations: [] },
    }));
    assert.match(packet, /PR packet is prepared but has not been published/);
    const approvals = formatWorkflowCompletion("pr-observer", completed({
        result: { status: "awaiting-human-review", prUrl: pr, ci: { status: "passed", headSha: "abc123" } },
    }));
    assert.match(approvals, /Required human review or repository approvals remain/);
    assert.match(approvals, /CI passed for the current PR head/);
    assert.match(approvals, /human reviewer must complete/);
    const blocked = formatWorkflowCompletion("pr-observer", completed({
        result: { status: "needs-owner-decision", blockerKind: "ci-dependencies", prUrl: pr, ci: { status: "failed", headSha: "abc123" }, summary: "Internal feed returned 403.", evidence: ["Artifact feed: HTTP 403 while resolving plugin"] },
    }));
    assert.match(blocked, /Internal feed returned 403/);
    assert.match(blocked, /feed or pipeline owner must check dependency access/);
    assert.match(blocked, /PR check evidence: Artifact feed: HTTP 403 while resolving plugin/);
    assert.doesNotMatch(blocked, /— blocked|Observer: needs-owner-decision/);
});

test("ready and approval labels without bound CI evidence never become a readiness or pass claim", () => {
    for (const status of ["ready-for-human-merge", "awaiting-human-review"]) {
        const prompt = formatWorkflowCompletion("pr-observer", completed({
            result: { status, prUrl: pr, ci: { status: "passed" } },
        }));
        assert.match(prompt, /CI reports a pass, but its PR head has not been confirmed/);
        assert.doesNotMatch(prompt, /CI passed for the current PR head|CI and observed PR checks passed|Ready for human/);
    }
    assert.equal(typeof deliveryStatusSummary("constructor"), "string");
});

class FakeSession {
    sessionId = "owning-session";
    handlers = new Set();
    runs = new Map();
    waits = new Map();
    messages = [];
    errors = [];
    logs = [];
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
                if (options?.afterSeq !== undefined) {
                    const records = this.progress.filter((record) => record.seq > options.afterSeq).slice(0, options.limit);
                    return {
                        records,
                        newestSeq: records.at(-1)?.seq ?? null,
                        hasMoreNewer: this.progress.some((record) => record.seq > (records.at(-1)?.seq ?? options.afterSeq)),
                    };
                }
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

    async log(message, options = {}) {
        this.logs.push({ message, ...options });
        if (options.level === "error") this.errors.push(message);
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
    function diagnosticFile() {
        const directory = path.join(root, ".github", "local-context", "code-feature", "completion", "diagnostics");
        return path.join(directory, readdirSync(directory).find((file) => file.endsWith(".jsonl")));
    }
    return { root, session, errors, install, stateFile, diagnosticFile };
}

test("fresh errors for one delivery incident produce one concise notice, with changed-cause and recovery notices", async (t) => {
    const { session, install, stateFile, diagnosticFile } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install({ reportError: undefined });
    await delivery.ready;
    const originalErrors = [];
    session.sendHook = () => {
        const error = new Error("Chat unavailable: C:\\private\\session-state\\full-diagnostic");
        originalErrors.push(error);
        throw error;
    };
    const milestone = { id: "incident", title: "Evidence", outcome: "completed", summary: "Evidence complete; implementation is next." };
    for (let i = 0; i < 3; i++) await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), /Chat unavailable/);
    session.sendHook = () => { throw originalErrors[0]; };
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), /Chat unavailable/);
    assert.equal(session.logs.length, 1, "Fresh Error objects and wrappers must not repeat the same notice");
    assert.equal(session.errors.length, 0, "A delivery incident must not create repeated session-error banners");
    assert.match(session.logs[0].message, /could not reach this chat/);
    assert.doesNotMatch(session.logs[0].message, /C:\\private|session-state|not acknowledged/);
    let diagnostics = readFileSync(diagnosticFile(), "utf8").trim().split("\n").map(JSON.parse);
    assert.deepEqual(diagnostics.map((record) => record.kind), ["failure", "repeat", "repeat", "repeat"]);
    assert.equal(diagnostics.at(-1).occurrences, 4, "A new retry is counted even when it reuses the same Error instance");
    assert.match(diagnostics[0].errors.at(-1).stack, /C:\\private\\session-state\\full-diagnostic/);
    assert.equal(new Set(diagnostics.map((record) => record.id)).size, 1);
    assert.equal(session.messages.length, 0);

    session.sendHook = () => { throw new Error("A different transport failure"); };
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), /different transport/);
    assert.equal(session.logs.length, 2);
    assert.match(session.logs[1].message, /cause has changed/);
    session.sendHook = undefined;
    await delivery.enqueueMilestone("run-1", "code-feature", milestone);
    await delivery.flush();
    assert.equal(session.logs.length, 3);
    assert.equal(session.logs[2].message, "Workflow updates can reach this chat again.");
    assert.equal(session.messages.length, 1);
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.delivered.length, 1);

    session.sendHook = () => { throw originalErrors[0]; };
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", { ...milestone, id: "new-incident" }), /Chat unavailable/);
    assert.equal(session.logs.length, 4, "The same Error object must be reportable again after recovery");
    diagnostics = readFileSync(diagnosticFile(), "utf8").trim().split("\n").map(JSON.parse);
    assert.equal(diagnostics.filter((record) => record.kind === "failure").length, 3);
    assert.equal(diagnostics.filter((record) => record.kind === "recovered").length, 1);
});

test("shared outbox failures coalesce across runs and temporary filenames without unrelated read recovery", async (t) => {
    const { session, errors, install } = fixture(t, [
        [completed({ status: "running" })], [completed({ runId: "run-2", status: "running" })],
    ]);
    let saved = { version: 1, sessionId: session.sessionId, runs: {} };
    let blocked = false;
    const failures = [];
    const delivery = install({ store: {
        load: () => saved,
        save(next) {
            if (blocked) {
                const error = Object.assign(new Error(`EPERM: rename C:\\private\\outbox.json.${100 + failures.length}.next`), {
                    code: "EPERM", syscall: "rename", path: `C:\\private\\outbox.json.${100 + failures.length}.next`, dest: "C:\\private\\outbox.json",
                });
                failures.push(error);
                throw error;
            }
            saved = structuredClone(next);
        },
    } });
    await delivery.ready;
    blocked = true;
    const milestone = { id: "save-incident", title: "Evidence", outcome: "completed", summary: "Evidence gathered." };
    for (const runId of ["run-1", "run-2", "run-1"]) {
        await assert.rejects(delivery.enqueueMilestone(runId, "code-feature", milestone), { code: "EPERM" });
    }
    assert.equal(errors.length, 1);
    assert.equal(errors[0], failures[0], "Injected reporters retain the original failure object");
    await delivery.track("run-1");
    await delivery.flush();
    assert.equal(session.logs.length, 0, "A successful progress read cannot recover a failed outbox save");
    assert.equal(session.messages.length, 0);
    blocked = false;
    await delivery.enqueueMilestone("run-2", "code-feature", milestone);
    assert.equal(session.logs.length, 1);
    assert.equal(session.logs[0].message, "Workflow updates can be saved again.");
    assert.equal(saved.runs["run-2"].milestones.delivered.length, 1);
    blocked = true;
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), { code: "EPERM" });
    assert.equal(errors.length, 2);
});

test("success for another run or operation does not clear a milestone delivery incident", async (t) => {
    const { session, install } = fixture(t, [
        [completed({ status: "running" })], [completed({ runId: "run-2", status: "running" })],
    ]);
    const delivery = install({ reportError: undefined });
    await delivery.ready;
    session.sendHook = (message) => {
        if (message.prompt.includes("[workflow-milestone:")) throw new Error("Milestone transport unavailable");
    };
    const milestone = { id: "still-blocked", title: "Evidence", outcome: "completed", summary: "Evidence gathered." };
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), /transport unavailable/);
    session.set(completed({ runId: "run-2" }));
    await delivery.track("run-2");
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /\[workflow-outcome:run-2:/);
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), /transport unavailable/);
    assert.equal(session.logs.length, 1, "Other saves, progress reads and terminal sends must not reset this incident");
    session.sendHook = undefined;
    await delivery.enqueueMilestone("run-1", "code-feature", milestone);
    assert.equal(session.logs.filter((record) => /can reach this chat again/.test(record.message)).length, 1);
});

test("unavailable settled progress is coalesced without hiding terminal failures or false recovery", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "error", error: "Actual workflow failure" })]]);
    let blocked = true;
    const readProgress = session.workflow.getRunProgress;
    session.workflow.getRunProgress = async (id, options) => {
        if (blocked && options.afterSeq === undefined) throw new Error("Private progress-tail diagnostic");
        return readProgress(id, options);
    };
    const delivery = install({ reportError: undefined });
    await delivery.ready;
    session.set(completed({ status: "error", attempt: 2, error: "Actual workflow failure" }));
    await delivery.track("run-1");
    await delivery.flush();
    assert.equal(session.logs.length, 1);
    assert.match(session.logs[0].message, /recorded outcome is still available/);
    assert.doesNotMatch(session.logs[0].message, /Retrying automatically|Private progress-tail/);
    assert.equal(session.messages.length, 2);
    for (const message of session.messages) assert.match(message.prompt, /Actual workflow failure/);
    blocked = false;
    session.set(completed({ status: "error", attempt: 3, error: "Actual workflow failure" }));
    await delivery.track("run-1");
    await delivery.flush();
    assert.equal(session.messages.length, 3);
    assert.equal(session.logs.length, 2);
    assert.equal(session.logs[1].message, "Workflow progress is available again.");
});

test("diagnostic write failures are explicit and coalesced without recursively logging through the failing channel", async (t) => {
    const { root, session, install, diagnosticFile } = fixture(t, [[completed({ status: "running" })]]);
    const consoleError = t.mock.method(console, "error", () => {});
    const delivery = install({ reportError: undefined });
    await delivery.ready;
    const blockedDirectory = path.join(root, ".github", "local-context", "code-feature", "completion", "diagnostics");
    writeFileSync(blockedDirectory, "A file blocks diagnostic directory creation.");
    session.sendHook = () => { throw new Error("Original private transport diagnostic"); };
    const milestone = { id: "diagnostic-failure", title: "Evidence", outcome: "completed", summary: "Evidence gathered." };
    for (let i = 0; i < 2; i++) await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone));
    assert.equal(session.logs.length, 1);
    assert.match(session.logs[0].message, /Detailed diagnostics could not be saved/);
    assert.equal(consoleError.mock.callCount(), 1);
    assert.match(consoleError.mock.calls[0].arguments[2].errors.at(-1).message, /Original private transport diagnostic/);
    rmSync(blockedDirectory);
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone));
    const record = JSON.parse(readFileSync(diagnosticFile(), "utf8").trim());
    assert.equal(record.kind, "repeat");
    assert.match(record.errors.at(-1).message, /Original private transport diagnostic/);
    session.sendHook = undefined;
    await delivery.enqueueMilestone("run-1", "code-feature", milestone);
    assert.equal(session.logs.length, 2);
});

test("private incident diagnostics rotate separately from the durable delivery snapshot", async (t) => {
    const { session, install, stateFile, diagnosticFile } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install({ reportError: undefined });
    await delivery.ready;
    session.sendHook = () => { throw new Error("Transport unavailable"); };
    const milestone = { id: "rotation", title: "Evidence", outcome: "completed", summary: "Evidence gathered." };
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone));
    const file = diagnosticFile();
    const previous = `${"x".repeat(1_048_575)}\n`;
    writeFileSync(file, previous);
    await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone));
    assert.equal(readFileSync(`${file}.previous`, "utf8"), previous);
    assert.equal(JSON.parse(readFileSync(file, "utf8").trim()).kind, "repeat");
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.pending.length, 1);
    assert.equal(session.logs.length, 1);
});

test("late catch-up joiners preserve the actual send-failure scope and do not duplicate incident notices", async (t) => {
    const { session, install, diagnosticFile } = fixture(t, [[completed({ status: "running" })]]);
    let saved = { version: 1, sessionId: session.sessionId, runs: {} };
    const cursorSaved = deferred();
    const delivery = install({ reportError: undefined, store: {
        load: () => saved,
        save(next) {
            saved = structuredClone(next);
            if (saved.runs["run-1"]?.milestones?.cursor === 1) cursorSaved.resolve();
        },
    } });
    await delivery.ready;
    const blocked = deferred();
    const entered = deferred();
    session.sendHook = () => { entered.resolve(); return blocked.promise; };
    const progress = await milestoneProgress("joined-failure", "Evidence");
    const enqueued = delivery.enqueueMilestone("run-1", "code-feature", milestoneFromProgress(progress));
    await entered.promise;
    session.progress = [progress];
    const catchingUp = delivery.track("run-1");
    await cursorSaved.promise;
    await new Promise(setImmediate);
    blocked.reject(new Error("Actual milestone transport failure"));
    await assert.rejects(enqueued, /Actual milestone transport failure/);
    await catchingUp;
    await delivery.flush();
    assert.equal(session.logs.length, 1);
    assert.match(session.logs[0].message, /could not reach this chat/);
    const diagnostics = readFileSync(diagnosticFile(), "utf8").trim().split("\n").map(JSON.parse);
    assert.equal(diagnostics.filter((record) => record.kind === "failure").length, 1);
    assert.equal(diagnostics[0].key, "milestones:run-1");
    assert.equal(saved.runs["run-1"].milestones.pending.length, 1);
});

test("verbose failed and deferred local evidence stays bounded in completion prompts and pending outboxes", async (t) => {
    for (const status of ["failed", "deferred"]) {
        const evidence = `SDK location not found. ${"Verbose build output. ".repeat(10_000)}`;
        const run = completed();
        run.result.verification = [{ targetId: "common", local: { status, evidence: [evidence] }, ci: { status: "not-observed" } }];
        const { session, install, stateFile } = fixture(t, [[run]]);
        let persistedPrompt;
        session.sendHook = () => { persistedPrompt = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].pending.prompt; };
        const delivery = install();
        await delivery.ready;
        await delivery.flush();
        assert.ok(session.messages[0].prompt.length < 4000, status);
        assert.equal(persistedPrompt, session.messages[0].prompt, "The durable-before-send prompt has the same bound");
        assert.match(persistedPrompt, /SDK location not found/);
        assert.equal(run.result.verification[0].local.evidence[0], evidence, "The full verification receipt is unchanged");
    }
});

async function holdWindowsOutbox(t, file) {
    const encodedPath = Buffer.from(file).toString("base64");
    const script = [
        "$ErrorActionPreference = 'Stop'",
        `$file = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encodedPath}'))`,
        "$stream = [IO.File]::Open($file, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)",
        "try { [Console]::WriteLine('locked'); [Console]::Out.Flush(); [Console]::ReadLine() | Out-Null } finally { $stream.Dispose() }",
    ].join("; ");
    const holder = spawn(path.join(process.env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), [
        "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64"),
    ], { stdio: ["pipe", "pipe", "pipe"] });
    const ready = deferred();
    const exited = deferred();
    let locked = false;
    let output = "";
    let errors = "";
    const timer = setTimeout(() => ready.reject(new Error("Timed out acquiring the Windows outbox sharing lock")), 20_000);
    holder.stdout.on("data", (chunk) => {
        output += chunk;
        if (output.includes("locked")) { locked = true; ready.resolve(); }
    });
    holder.stderr.on("data", (chunk) => { errors += chunk; });
    holder.on("error", (error) => { ready.reject(error); exited.resolve({ error }); });
    holder.on("exit", (code) => {
        if (!locked) ready.reject(new Error(`Windows lock process exited before acquiring the file: ${code}; ${errors}`));
        exited.resolve({ code });
    });
    const release = async () => {
        if (!holder.stdin.destroyed && !holder.stdin.writableEnded) holder.stdin.end("\n");
        const result = await exited.promise;
        if (result.error) throw result.error;
        assert.equal(result.code, 0, errors);
    };
    t.after(release);
    try { await ready.promise; }
    finally { clearTimeout(timer); }
    return release;
}

test("Windows sharing locks retry atomically without blocking the event loop or admitting unpersisted messages",
    { skip: process.platform !== "win32" }, async (t) => {
    const { session, install, stateFile, errors } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install();
    await delivery.ready;
    const file = stateFile();
    const before = readFileSync(file, "utf8");
    const release = await holdWindowsOutbox(t, file);
    try {
        const sending = delivery.enqueueMilestone("run-1", "code-feature", {
            id: "windows-lock", title: "Evidence", outcome: "completed", summary: "Evidence complete; implementation is next.",
        }).then((value) => ({ value }), (error) => ({ error }));
        await new Promise((resolve) => setTimeout(resolve, 100));
        assert.equal(session.messages.length, 0, "A notice must not be sent before its outbox commit succeeds");
        assert.equal(readFileSync(file, "utf8"), before, "A sharing violation must preserve the previous complete snapshot");
        await release();
        const result = await sending;
        if (result.error) throw result.error;
    } finally { await release(); }
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.equal(errors.length, 0, "A recovered transient lock is not a failed chat handoff");
    assert.equal(JSON.parse(readFileSync(file, "utf8")).runs["run-1"].milestones.delivered.length, 1);
    assert.equal(readdirSync(path.dirname(file)).some((name) => name.endsWith(".next")), false);
});

test("persistent Windows sharing failures remain explicit and retryable without false acknowledgement",
    { skip: process.platform !== "win32" }, async (t) => {
    const { session, install, stateFile } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install();
    await delivery.ready;
    const file = stateFile();
    const before = readFileSync(file, "utf8");
    const release = await holdWindowsOutbox(t, file);
    const milestone = { id: "persistent-lock", title: "Evidence", outcome: "completed", summary: "Evidence gathered." };
    try {
        await assert.rejects(delivery.enqueueMilestone("run-1", "code-feature", milestone), (error) =>
            ["EPERM", "EACCES", "EBUSY"].includes(error.code));
        assert.equal(session.messages.length, 0);
        assert.equal(readFileSync(file, "utf8"), before);
        assert.equal(readdirSync(path.dirname(file)).some((name) => name.endsWith(".next")), false);
    } finally { await release(); }
    await delivery.enqueueMilestone("run-1", "code-feature", milestone);
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.equal(JSON.parse(readFileSync(file, "utf8")).runs["run-1"].milestones.delivered.length, 1);
});

test("asynchronous outbox writes serialize across runs and concurrent duplicate notices", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "running" })], [completed({ runId: "run-2", status: "running" })]]);
    let saved = { version: 1, sessionId: session.sessionId, runs: {} };
    let hold = false;
    let liveWrites = 0;
    let maximumWrites = 0;
    const entered = deferred();
    const release = deferred();
    const delivery = install({ store: {
        load: () => saved,
        async save(next) {
            liveWrites++;
            maximumWrites = Math.max(maximumWrites, liveWrites);
            try {
                if (hold) { entered.resolve(); await release.promise; }
                saved = structuredClone(next);
            } finally { liveWrites--; }
        },
    } });
    await delivery.ready;
    hold = true;
    const milestone = { id: "concurrent", title: "Evidence", outcome: "completed", summary: "Evidence gathered." };
    const one = delivery.enqueueMilestone("run-1", "code-feature", milestone);
    const two = delivery.enqueueMilestone("run-2", "code-feature", milestone);
    const duplicate = delivery.enqueueMilestone("run-1", "code-feature", milestone);
    await entered.promise;
    assert.equal(session.messages.length, 0);
    hold = false;
    release.resolve();
    await Promise.all([one, two, duplicate]);
    await delivery.flush();
    assert.equal(maximumWrites, 1);
    assert.equal(session.messages.length, 2);
    assert.equal(saved.runs["run-1"].milestones.delivered.length, 1);
    assert.equal(saved.runs["run-2"].milestones.delivered.length, 1);
});

async function milestoneProgress(id, title, outcome = "completed", summary = "Source review only; CI is unverified.", link) {
    let message;
    await recordMilestone({
        async step(_key, producer) { return producer(); },
        log(value) { message = value; },
    }, { id, title, outcome, summary, ...(link ? { prUrl: link } : {}) });
    return { seq: 1, attempt: 1, kind: "log", phaseId: "p0", recordedAt: 1000, text: message };
}

test("completed phases appear in chat while the workflow is running, not just at settlement", async (t) => {
    const { session, install, stateFile } = fixture(t, [[completed({ status: "running" })]]);
    session.progress = [{ seq: 1, attempt: 1, kind: "phase", phaseId: "p0", text: "Build initial implementation" }];
    const delivery = install();
    await delivery.ready;
    assert.equal(session.messages.length, 0, "Entering a phase does not prove it completed");
    session.progress.push({ ...await milestoneProgress("evidence", "Evidence and requirements"), seq: 2 });
    session.emit("workflow.run_updated", { runId: "run-1", revision: 2 });
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /Evidence and requirements\./);
    assert.match(session.messages[0].prompt, /CI is unverified/);
    assert.match(session.messages[0].prompt, /not a terminal result/);
    assert.match(session.messages[0].prompt, /\[workflow-milestone:run-1:[a-f0-9]{64}\]/);
    assert.equal(session.messages[0].source, "system");
    assert.equal(session.messages[0].mode, "immediate");
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.delivered.length, 1);
    for (let revision = 3; revision < 10; revision++) session.emit("workflow.run_updated", { runId: "run-1", revision });
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    session.progress.push({ ...await milestoneProgress("published", "Pull request published", "completed",
        "Publication completed. CI is not yet verified.", adoPr), seq: 3 });
    session.emit("workflow.run_updated", { runId: "run-1", revision: 10 });
    await delivery.flush();
    assert.deepEqual(prLinkUrls(session.messages[1].prompt), [adoPr]);
    session.set(completed());
    session.emit("workflow.run_settled", { runId: "run-1" });
    await delivery.flush();
    assert.equal(session.messages.length, 3);
    assert.match(session.messages[2].prompt, /\[workflow-outcome:/);
});

test("milestone catch-up pages all progress and orders completed phases before the final outcome", async (t) => {
    const { session, install } = fixture(t, [[completed()]]);
    const first = await milestoneProgress("evidence", "Evidence");
    const last = await milestoneProgress("review", "Review");
    session.progress = Array.from({ length: 205 }, (_, i) => ({
        seq: i + 1, kind: "log", attempt: 1, text: "Ordinary progress is not a milestone.",
    }));
    session.progress[0] = first;
    session.progress[204] = { ...last, seq: 205 };
    const delivery = install();
    await delivery.ready;
    assert.equal(session.messages.length, 3);
    assert.match(session.messages[0].prompt, /Evidence\./);
    assert.match(session.messages[1].prompt, /Review\./);
    assert.match(session.messages[2].prompt, /\[workflow-outcome:/);
    assert.deepEqual(session.calls.filter(([name]) => name === "getRunProgress").map(([, , options]) => options.afterSeq), [0, 100, 200]);
    delivery.dispose();
    await install().ready;
    assert.equal(session.messages.length, 3, "Restart replays neither acknowledged phase nor terminal updates");
});

test("failed milestone delivery survives restart, retains order, and does not lose later phases", async (t) => {
    const { session, install, stateFile, errors } = fixture(t, [[completed({ status: "running" })]]);
    session.progress = [await milestoneProgress("builder", "Initial implementation", "completed"),
        { ...await milestoneProgress("verification", "Local verification", "deferred", "Local command timed out; tests require CI."), seq: 2 }];
    let failedPrompt;
    session.sendHook = (message) => { failedPrompt = message.prompt; throw new Error("Chat temporarily unavailable"); };
    const first = install();
    await first.ready;
    assert.equal(session.messages.length, 0);
    const state = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones;
    assert.equal(state.cursor, 2);
    assert.equal(state.pending.length, 2);
    assert.deepEqual(state.delivered, []);
    assert.ok(errors.some((error) => /milestone was not acknowledged/.test(error.message)));
    first.dispose();
    session.sendHook = undefined;
    await install().ready;
    assert.equal(session.messages.length, 2);
    assert.equal(session.messages[0].prompt, failedPrompt);
    assert.match(session.messages[1].prompt, /Local verification\./);
    assert.match(session.messages[1].prompt, /Local command timed out; tests require CI/);
    assert.match(session.messages[1].prompt, /tests require CI/);
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.pending.length, 0);
});

test("replayed and concurrent milestone events announce the same durable identity only once", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install();
    await delivery.ready;
    const record = await milestoneProgress("source-review-1", "Independent source review, round 1", "blocked", "One finding needs a correction; no local pass implied.");
    session.progress.push(record);
    const sending = deferred();
    const admitted = deferred();
    session.sendHook = async () => { sending.resolve(); await admitted.promise; };
    session.emit("workflow.run_updated", { runId: "run-1" });
    await sending.promise;
    session.progress.push({ ...record, seq: 2, attempt: 2 });
    for (let i = 0; i < 10; i++) session.emit("workflow.run_updated", { runId: "run-1", revision: i });
    admitted.resolve();
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /round 1\./);
});

test("ordinary or malformed progress never fabricates completed phases, and missing invalidations catch up", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "running" })]]);
    session.progress = [
        { seq: 1, kind: "phase", text: "Prepare and create pull requests" },
        { seq: 2, kind: "log", text: "BUILD SUCCESSFUL" },
        { seq: 3, kind: "log", text: '[workflow-milestone-v1]{"id":"fake","title":"Fake","outcome":"passed","summary":"Not a supported receipt"}' },
        { seq: 4, kind: "log", text: "[workflow-milestone-v1]{invalid" },
    ];
    const delivery = install();
    await delivery.ready;
    assert.equal(session.messages.length, 0);
    session.progress.push({ ...await milestoneProgress("publish", "Pull request published", "completed", "Publication only."), seq: 5 });
    // A session resume reconciles a missed progress invalidation; no workflow is restarted.
    session.emit("session.resume");
    await delivery.reconcile();
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /Pull request published\./);
    assert.equal(session.calls.filter(([method]) => ["run", "resume", "pause"].includes(method)).length, 0);
});

test("a progress-page failure cannot hide a terminal blocker, and later catch-up remains possible", async (t) => {
    const { session, install, errors } = fixture(t, [[completed({ status: "error", error: "Publication rejected" })]]);
    const getProgress = session.workflow.getRunProgress;
    session.workflow.getRunProgress = async () => { throw new Error("Progress store offline"); };
    const delivery = install();
    await delivery.ready;
    assert.ok(session.messages.some((message) => message.prompt.includes("Publication rejected")));
    assert.ok(errors.some((error) => error.message.includes("Progress store offline")));
    session.workflow.getRunProgress = getProgress;
    session.progress = [await milestoneProgress("review", "Source review")];
    await delivery.reconcile();
    assert.equal(session.messages.length, 2);
    assert.match(session.messages[1].prompt, /Source review\./);
});

test("milestone producer journals explicit completion once and does not log it again on replay", async () => {
    const journal = new Map();
    const records = [];
    const createContext = () => ({
        async step(key, producer) {
            if (!journal.has(key)) journal.set(key, await producer());
            return journal.get(key);
        },
        log(text) { records.push({ seq: records.length + 1, kind: "log", text }); },
    });
    const input = { id: "phase-evidence", title: "Evidence", outcome: "completed", summary: "Requirements resolved; source review is still pending." };
    const context = createContext();
    await Promise.all([recordMilestone(context, input), recordMilestone(context, input)]);
    await recordMilestone(createContext(), input);
    assert.equal(records.length, 1);
    assert.equal(milestoneFromProgress(records[0]).title, "Evidence");
    assert.equal(milestoneFromProgress({ kind: "log", text: `untrusted prefix ${records[0].text}` }), null);
    await assert.rejects(recordMilestone(context, { ...input, outcome: "passed" }), /Invalid workflow milestone/);
    await assert.rejects(recordMilestone(context, { ...input, prUrl: "http://github.com/AzureAD/example/pull/42" }), /Invalid workflow milestone/);
});

test("the workflow producer pushes phase updates without run events, progress reads, or terminal settlement", async (t) => {
    const { session, install, stateFile } = fixture(t);
    const delivery = install();
    await delivery.ready;
    session.workflow.getRun = async () => { throw new Error("Run RPC must not be required for a live milestone push"); };
    session.workflow.getRunProgress = async () => { throw new Error("Progress RPC must not be required for a live milestone push"); };
    const journal = new Map();
    const records = [];
    const context = {
        async step(key, producer) {
            if (!journal.has(key)) journal.set(key, await producer());
            return journal.get(key);
        },
        log(text) { records.push(text); },
        onMilestone: (receipt) => delivery.enqueueMilestone("live-run", "code-feature", receipt),
    };
    const receipt = { id: "evidence-done", title: "Evidence", outcome: "completed", summary: "Source evidence gathered; implementation and CI remain pending." };
    await recordMilestone(context, receipt);
    await delivery.flushMilestones();
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /Evidence\./);
    assert.match(session.messages[0].prompt, /implementation and CI remain pending/);
    assert.equal(session.calls.filter(([method]) => ["getRun", "getRunProgress", "waitForRun"].includes(method)).length, 0);
    const state = JSON.parse(readFileSync(stateFile(), "utf8")).runs["live-run"].milestones;
    assert.equal(state.pending.length, 0);
    assert.equal(state.delivered.length, 1);
    const key = state.delivered[0];
    assert.equal(state.receipts[key].messageId, "message-1");
    assert.ok(state.receipts[key].admittedAt >= state.receipts[key].enqueuedAt);
    assert.equal(state.receipts[key].observedAt, undefined, "Admission alone is not proof of transcript delivery");
    session.emit("user.message", { source: "system", content: session.messages[0].prompt });
    await delivery.flush();
    const observed = JSON.parse(readFileSync(stateFile(), "utf8")).runs["live-run"].milestones.receipts[key];
    assert.ok(observed.observedAt >= observed.enqueuedAt);
    assert.equal(session.messages.length, 1);
    assert.equal(records.length, 1);
});

test("direct producer and concurrent progress catch-up share ordered, deduplicated milestone admission", async (t) => {
    const { session, install } = fixture(t, [[completed({ status: "running" })]]);
    const delivery = install();
    await delivery.ready;
    const record = await milestoneProgress("review", "Source review");
    const receipt = milestoneFromProgress(record);
    const sending = deferred();
    const release = deferred();
    session.sendHook = async () => { sending.resolve(); await release.promise; };
    delivery.enqueueMilestone("run-1", "code-feature", receipt);
    await sending.promise;
    session.progress = [record];
    session.emit("workflow.run_updated", { runId: "run-1" });
    delivery.enqueueMilestone("run-1", "code-feature", { id: "publish", title: "PR published", outcome: "completed", summary: "CI pending.", prUrl: adoPr });
    release.resolve();
    await delivery.flush();
    assert.equal(session.messages.length, 2);
    assert.match(session.messages[0].prompt, /Source review\./);
    assert.deepEqual(prLinkUrls(session.messages[1].prompt), [adoPr]);
    delivery.enqueueMilestone("run-1", "code-feature", receipt);
    await delivery.flushMilestones();
    assert.equal(session.messages.length, 2);
});

test("a direct enqueue during empty catch-up teardown is admitted before flushing can finish", async (t) => {
    const { session, install, stateFile } = fixture(t, [[completed({ status: "running" })]]);
    let queued = false;
    let admission;
    let delivery;
    session.workflow.getRunProgress = async () => {
        if (!queued) {
            queued = true;
            queueMicrotask(() => queueMicrotask(() => {
                admission = delivery.enqueueMilestone("run-1", "code-feature", {
                    id: "late-handoff", title: "PR published", outcome: "completed", summary: "CI pending.", prUrl: adoPr,
                });
            }));
        }
        return { records: [] };
    };
    delivery = install();
    await delivery.ready;
    await admission;
    await delivery.flushMilestones();
    assert.equal(session.messages.length, 1);
    assert.deepEqual(prLinkUrls(session.messages[0].prompt), [adoPr]);
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.pending.length, 0);
});

test("live send retry does not wait behind a stalled status RPC or require a lifecycle event", async (t) => {
    const { session, install } = fixture(t, [], { retryDelayMs: 5 });
    const delivery = install();
    await delivery.ready;
    const blockedRead = deferred();
    session.set(completed({ status: "running" }));
    session.workflow.getRun = () => blockedRead.promise;
    void delivery.track("run-1", "code-feature");
    let attempts = 0;
    const admitted = deferred();
    session.sendHook = () => {
        if (++attempts === 1) throw new Error("Transient chat outage");
        admitted.resolve();
    };
    delivery.enqueueMilestone("run-1", "code-feature", {
        id: "build", title: "Initial implementation", outcome: "completed", summary: "Source handoff, not a local test pass.",
    });
    const timeout = setTimeout(() => admitted.reject(new Error("Direct send retry was blocked by the status read")), 2000);
    try { await admitted.promise; }
    finally { clearTimeout(timeout); blockedRead.resolve(completed({ status: "running" })); }
    await delivery.flush();
    assert.equal(session.messages.length, 1);
    assert.match(session.messages[0].prompt, /Initial implementation/);
});

test("producer delivery failure preserves durable source progress and surfaces explicit catch-up diagnostics", async () => {
    const logs = [];
    const ctx = {
        step: async (_key, producer) => producer(),
        log: async (text) => logs.push(text),
        onMilestone() { throw new Error("Outbox disk unavailable"); },
    };
    const receipt = await recordMilestone(ctx, { id: "review", title: "Review", outcome: "completed", summary: "Source review only." });
    assert.equal(receipt.id, "review");
    assert.equal(milestoneFromProgress({ kind: "log", text: logs[0] }).id, "review");
    assert.match(logs[1], /Milestone chat handoff failed.*Outbox disk unavailable/);
    assert.match(logs[1], /durable progress receipt remains available/);
});

test("a stage awaits prompt admission before continuing, without waiting for caller model completion", async (t) => {
    const { session, install } = fixture(t);
    const delivery = install();
    await delivery.ready;
    const release = deferred();
    const sending = deferred();
    session.sendHook = async () => { sending.resolve(); await release.promise; };
    const logs = [];
    let stageAdvanced = false;
    const stage = recordMilestone({
        step: async (_key, producer) => producer(),
        log: async (text) => logs.push(text),
        onMilestone: (receipt) => delivery.enqueueMilestone("live-run", "code-feature", receipt),
    }, { id: "build", title: "Initial implementation", outcome: "completed", summary: "Source handoff only." })
        .then(() => { stageAdvanced = true; });
    await sending.promise;
    assert.equal(stageAdvanced, false, "The next synchronous command must not starve chat admission");
    release.resolve();
    await stage;
    assert.equal(stageAdvanced, true);
    assert.equal(session.messages.length, 1);
    assert.equal(logs.length, 1);
    assert.equal(session.calls.filter(([method]) => method === "getRunProgress").length, 0);
});

test("an unavailable chat cannot stall source execution indefinitely or falsely acknowledge a milestone", async (t) => {
    const { session, install, stateFile, errors } = fixture(t, [], { admissionTimeoutMs: 10 });
    const delivery = install();
    await delivery.ready;
    const release = deferred();
    session.sendHook = () => release.promise;
    const logs = [];
    await recordMilestone({
        step: async (_key, producer) => producer(),
        log: async (text) => logs.push(text),
        onMilestone: (receipt) => delivery.enqueueMilestone("live-run", "code-feature", receipt),
    }, { id: "review", title: "Source review", outcome: "completed", summary: "Execution unverified." });
    const before = JSON.parse(readFileSync(stateFile(), "utf8")).runs["live-run"].milestones;
    assert.equal(before.delivered.length, 0);
    assert.equal(before.pending.length, 1);
    assert.equal(logs.length, 1, "A delayed admission is not a failed producer handoff");
    assert.deepEqual(errors, []);
    assert.deepEqual(session.logs, [], "Ordinary wait timing is not surfaced to the session");
    assert.ok(JSON.parse(readFileSync(stateFile(), "utf8")).runs["live-run"].notificationDiagnostics["chat admission"]);
    release.resolve();
    await delivery.flushMilestones();
    assert.equal(session.messages.length, 1, "The still-in-flight admission is not duplicated on timeout");
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["live-run"].milestones.pending.length, 0);
});

test("slow catch-up keeps timing diagnostics private and shares one in-flight progress read across timeouts", async (t) => {
    const { session, install, stateFile } = fixture(t, [[completed({ status: "running" })]], { admissionTimeoutMs: 5 });
    const progress = deferred();
    let reads = 0;
    session.workflow.getRunProgress = () => { reads++; return progress.promise; };
    const delivery = install({ reportError: undefined });
    try {
        await delivery.ready;
        assert.deepEqual(session.errors, [], "An observation-window timeout must not become a session.error notification");
        assert.deepEqual(session.logs, []);
        const diagnostics = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].notificationDiagnostics;
        assert.equal(diagnostics["milestone catch-up"].windowMs, 5);
        assert.ok(diagnostics["milestone catch-up"].elapsedMs >= 5);
        for (let attempt = 0; attempt < 3; attempt++) await delivery.track("run-1", "code-feature");
        assert.equal(reads, 1, "Timeouts must not duplicate the unfinished catch-up RPC");
        assert.deepEqual(session.logs, [], "No session notification is emitted for ordinary wait timing");
        assert.deepEqual(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].notificationDiagnostics, diagnostics,
            "The same unfinished operation does not repeatedly write diagnostics");
        assert.ok(session.waits.has("run-1"), "Settlement observation must remain installed");
    } finally {
        progress.resolve({ records: [] });
        await delivery.flush();
    }
});

test("slow producer admission is a bounded delayed result, not a failed handoff or session error", async (t) => {
    const { session, install, stateFile } = fixture(t, [], { admissionTimeoutMs: 5 });
    const delivery = install({ reportError: undefined });
    await delivery.ready;
    const release = deferred();
    let attempts = 0;
    session.sendHook = () => { attempts++; return release.promise; };
    const logs = [];
    let handoff;
    try {
        await recordMilestone({
            step: async (_key, producer) => producer(),
            log: async (message) => logs.push(message),
            onMilestone: async (receipt) => { handoff = await delivery.enqueueMilestone("run-1", "code-feature", receipt); },
        }, { id: "toolchain", title: "Build toolchain selected", outcome: "completed", summary: "JDK selected; no verification claim." });
        assert.deepEqual(session.errors, []);
        assert.equal(handoff.status, "delayed");
        assert.equal(logs.length, 1, "A timeout must not write a failed milestone handoff into durable workflow progress");
        assert.deepEqual(session.logs, [], "No user-facing delay message is emitted");
        const state = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"];
        assert.equal(state.milestones.pending.length, 1);
        assert.equal(state.milestones.delivered.length, 0);
        assert.equal(state.milestones.lastError, undefined);
        assert.equal(attempts, 1);
    } finally {
        release.resolve();
        await delivery.flushMilestones();
    }
    assert.equal(session.messages.length, 1);
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.pending.length, 0);
});

test("startup catch-up can time out before a directly delivered milestone without creating an error or duplicate notice", async (t) => {
    const { session, install, stateFile } = fixture(t, [[completed({ status: "running" })]], { admissionTimeoutMs: 5 });
    const progress = deferred();
    session.workflow.getRunProgress = () => progress.promise;
    const delivery = install({ reportError: undefined });
    try {
        await delivery.ready;
        const record = await milestoneProgress("toolchain", "Build toolchain selected");
        await delivery.enqueueMilestone("run-1", "code-feature", milestoneFromProgress(record));
        assert.deepEqual(session.errors, []);
        assert.equal(session.messages.length, 1, "The stalled progress read does not block the direct producer");
        progress.resolve({ records: [record], hasMoreNewer: false });
        await delivery.track("run-1", "code-feature");
        await delivery.flush();
        const state = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"];
        assert.equal(state.milestones.cursor, 1);
        assert.equal(state.milestones.delivered.length, 1);
        assert.equal(state.milestones.pending.length, 0);
        assert.equal(state.lastError, undefined);
        assert.equal(state.milestones.lastError, undefined);
        assert.equal(session.messages.length, 1, "Late catch-up cannot duplicate the admitted direct receipt");
    } finally {
        progress.resolve({ records: [] });
        await delivery.flush();
    }
});

for (const stage of ["catch-up", "admission"]) {
    test(`a real ${stage} failure after the delay boundary remains explicit without a session-error banner`, { timeout: 3000 }, async (t) => {
        const { session, install, diagnosticFile } = fixture(t, stage === "catch-up" ? [[completed({ status: "running" })]] : [],
            { admissionTimeoutMs: 5 });
        const blocked = deferred();
        const failure = deferred();
        t.mock.method(console, "error", () => {});
        const log = session.log.bind(session);
        session.log = async (message, options) => {
            await log(message, options);
            if (options?.level === "info") failure.resolve();
        };
        if (stage === "catch-up") session.workflow.getRunProgress = () => blocked.promise;
        else session.sendHook = () => blocked.promise;
        const delivery = install({ reportError: undefined });
        try {
            await delivery.ready;
            if (stage === "admission") await delivery.enqueueMilestone("run-1", "code-feature", {
                id: "toolchain", title: "Build toolchain selected", outcome: "completed", summary: "Execution unverified.",
            });
            assert.equal(session.errors.length, 0, "A slow operation is not a failed operation");
            blocked.reject(new Error("Actual transport failure after timeout"));
            await failure.promise;
            assert.equal(session.errors.length, 0);
            assert.equal(session.logs.length, 1);
            assert.doesNotMatch(session.logs[0].message, /Actual transport failure after timeout/);
            assert.match(readFileSync(diagnosticFile(), "utf8"), /Actual transport failure after timeout/);
        } finally {
            blocked.resolve({ records: [] });
            delivery.dispose();
            await delivery.flush();
        }
    });
}

for (const initiallyRunning of [false, true]) {
test(`a hung milestone cannot block terminal delivery (${initiallyRunning ? "settlement during stalled reconciliation" : "already settled"}) or duplicate in-flight sends`, async (t) => {
    const { session, install, stateFile, errors } = fixture(t, [], { admissionTimeoutMs: 10, retryDelayMs: 5 });
    const delivery = install();
    await delivery.ready;
    session.set(completed({ status: initiallyRunning ? "running" : "completed" }));
    const release = deferred();
    const terminal = deferred();
    let milestoneAttempts = 0;
    session.sendHook = (message) => {
        if (message.prompt.includes("[workflow-milestone:")) { milestoneAttempts++; return release.promise; }
        terminal.resolve();
    };
    const timeout = setTimeout(() => terminal.reject(new Error("Terminal delivery is blocked by a hung milestone send")), 2000);
    try {
        const admission = await delivery.enqueueMilestone("run-1", "code-feature", {
            id: "hung", title: "PR published", outcome: "completed", summary: "CI unverified.", prUrl: adoPr,
        });
        assert.equal(admission.status, "delayed");
        const inspection = delivery.track("run-1", "code-feature");
        if (initiallyRunning) {
            await inspection;
            assert.ok(session.waits.has("run-1"), "A blocked milestone cannot prevent installing the SDK settlement watcher");
            session.set(completed());
            session.waits.get("run-1").resolve(completed());
        }
        await terminal.promise;
        const state = JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"];
        assert.equal(state.milestones.pending.length, 1);
        assert.equal(state.milestones.delivered.length, 0);
        assert.ok(state.pending || state.delivered.length, "Terminal receipt is durable independently of the stalled milestone");
        assert.equal(milestoneAttempts, 1);
        assert.deepEqual(errors, [], "A hung operation has not reported an actual failure");
    } finally {
        clearTimeout(timeout);
        release.resolve();
        await delivery.flush();
    }
    assert.equal(milestoneAttempts, 1);
    assert.equal(session.messages.filter((message) => message.prompt.includes("[workflow-outcome:")).length, 1);
    assert.equal(JSON.parse(readFileSync(stateFile(), "utf8")).runs["run-1"].milestones.pending.length, 0);
});
}

for (const fallback of [false, true]) {
    test(`settlement at reconciliation-worker teardown is not lost (${fallback ? "SDK wait fallback" : "event"})`, async (t) => {
        // Sweep scheduling boundaries to cover the final dirty-check/promise-cleanup gap.
        for (let depth = 1; depth <= 16; depth++) {
            const { session, install } = fixture(t, [[completed({ status: "running" })]]);
            const delivery = install();
            await delivery.ready;
            let queued = false;
            session.workflow.getRunProgress = async () => {
                if (!queued) {
                    queued = true;
                    let remaining = depth;
                    const advance = () => {
                        if (--remaining > 0) { queueMicrotask(advance); return; }
                        session.set(completed());
                        if (fallback) session.waits.get("run-1").resolve(completed());
                        else session.emit("workflow.run_settled", { runId: "run-1" });
                    };
                    queueMicrotask(advance);
                }
                return { records: [] };
            };
            await delivery.track("run-1", "code-feature");
            await new Promise((resolve) => setImmediate(resolve));
            await delivery.flush();
            assert.equal(session.messages.filter((message) => message.prompt.includes("[workflow-outcome:")).length, 1, `Lost settlement at microtask boundary ${depth}`);
            delivery.dispose();
        }
    });
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
    assert.match(message.prompt, /workflow code-feature, run run-1/);
    assert.match(message.prompt, /attempt 1/);
    assert.deepEqual(prLinkUrls(message.prompt), [pr]);
    assert.match(message.prompt, /CI and observed PR checks passed for every published PR/);
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
    assert.match(prompt, /Observer stopped after timeout/);
    assert.match(prompt, /Merge readiness is not confirmed for every PR/);
    assert.match(prompt, /Continue observing this PR with the remaining repair allowance/);
    assert.match(prompt, /Local verification is incomplete/);
    assert.doesNotMatch(prompt, /CI and observed PR checks passed for every published PR/);
});

test("completed NO-GO and missing results remain non-successful deliverables", async (t) => {
    const { session, install } = fixture(t, [
        [completed({ result: { recommendation: "NO-GO", reasons: ["Unit test assertion failed"], questions: ["Which requirement applies?"] } })],
        [completed({ runId: "run-2", result: undefined })],
    ]);
    await install().ready;
    assert.equal(session.messages.length, 2);
    const prompt = session.messages.find((message) => message.prompt.includes("run run-1")).prompt;
    assert.match(prompt, /Work stopped with unresolved issues/);
    assert.match(prompt, /Unit test assertion failed/);
    assert.match(prompt, /Needs input: Which requirement applies/);
    for (const message of session.messages) {
        assert.match(message.prompt, /no published URL recorded/);
        assert.match(message.prompt, /CI has not been confirmed/);
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
        assert.match(prompt, new RegExp(`settled state ${status}`));
        assert.match(prompt, /Verification is incomplete/);
        assert.match(prompt, /publication\/verification not confirmed/);
        assert.deepEqual(prLinkUrls(prompt), [adoPr]);
        assert.match(prompt, /Requires an approved compatible JDK/);
        assert.doesNotMatch(prompt, /stale result|Stale progress/);
        assert.match(prompt, status === "paused" ? /resume this same run run-1/ : /establish resumability/);
        if (status === "paused") assert.match(prompt, /Paused checkpoint: choose-jdk/);
    });
}

test("a paused prerequisite produces an immediate actionable ask_user instruction with its exact recorded question", async (t) => {
    const records = [];
    const question = "No compatible JDK is installed. Provide a JDK 21 directory after installation.";
    await recordHumanInputRequest({ log: async (text) => records.push({ seq: 1, attempt: 1, kind: "log", text }) }, {
        key: "missing-toolchain", questions: [question], recoveryPath: ".github\\local-context\\code-feature\\run-1.json",
    });
    const run = completed({ status: "paused", result: undefined, pauseInfo: { key: "missing-toolchain" } });
    const { session, install } = fixture(t, [[run]]);
    session.progress = records;
    await install().ready;
    const message = session.messages[0].prompt;
    assert.match(message, /open an actionable ask_user question now/);
    assert.match(message, /do not end with only a paused-status report/);
    assert.ok(message.includes(question));
    assert.match(message, /Confirm this same run is still paused/);
    assert.match(message, /preserve any existing input request/);
    assert.match(message, /Run-scoped recovery path:/);
    assert.match(message, /resume this same run run-1/);
    assert.equal(humanInputRequestFromProgress(records, "another-checkpoint"), null);
});

test("older paused runs without structured input still require a question, not silent parking", () => {
    const message = formatWorkflowCompletion("code-feature", completed({
        status: "paused", result: undefined, reason: "Android SDK is missing",
    }));
    assert.match(message, /ask_user question now/);
    assert.match(message, /specific missing decision or prerequisite/);
    assert.match(message, /do not silently park the caller/);
});

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
    assert.match(session.messages[0].prompt, /PR observation finished/);
    assert.match(session.messages[0].prompt, /developer must resolve the recorded blocker or scope decision/);
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
    assert.match(session.messages[0].prompt, /attempt 2; settled state completed/);
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
    assert.match(prompt, /CI failed; the reported failures need attention/);
    assert.match(prompt, /common: Local verification is incomplete/);
    assert.match(prompt, /CI failed for the current PR head\. Observed head: abc123/);
    assert.match(prompt, /Build hit the local time limit/);
    assert.match(prompt, /PR status publication failed: PR edit rejected/);
    assert.match(prompt, /The PR body may be stale/);
    assert.match(prompt, /A continuation is available for this PR/);
    assert.match(prompt, /this handoff is not a running observer/);
    assert.match(prompt, /do not merge/);
    assert.doesNotMatch(prompt, /CI and observed PR checks passed for every published PR/);
});

test("legacy observer status without explicit CI evidence cannot claim current-head success", async (t) => {
    const run = completed();
    delete run.result.observations[0].ci;
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    assert.match(session.messages[0].prompt, /CI has not been checked yet\. Observed head: unavailable/);
    assert.doesNotMatch(session.messages[0].prompt, /CI and observed PR checks passed for every published PR/);
});

test("failed PR status publication prevents a readiness claim even when CI passed", async (t) => {
    const run = completed();
    run.result.observations[0].statusPublication = { status: "failed", error: "Provider unavailable" };
    const { session, install } = fixture(t, [[run]]);
    await install().ready;
    assert.match(session.messages[0].prompt, /PR status publication failed/);
    assert.match(session.messages[0].prompt, /developer must restore the PR status update/);
    assert.doesNotMatch(session.messages[0].prompt, /CI and observed PR checks passed for every published PR/);
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
        assert.ok(result.settledSummary.includes(deliveryStatusSummary(deliveryStatus)));
        assert.ok(session.messages[0].prompt.includes(deliveryStatusSummary(deliveryStatus)));
        assert.doesNotMatch(result.settledSummary, /CI and observed PR checks passed for every published PR/);
    });
}
