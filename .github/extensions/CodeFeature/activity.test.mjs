// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import test from "node:test";
import { withWorkflowActivity } from "./activity.mjs";

function fixture() {
    const controller = new AbortController();
    const calls = [];
    const tasks = new Map();
    const session = {
        log: async (message) => calls.push(["log", message]),
        rpc: { tasks: {
            register: async (args) => {
                calls.push(["register", args]);
                const task = { id: `task-${tasks.size}`, sequence: 0, status: "running" };
                tasks.set(task.id, task);
                return { created: true, task: { ...task } };
            },
            update: async (args) => {
                calls.push(["update", args]);
                const task = tasks.get(args.id);
                assert.equal(args.sequence, task.sequence + 1);
                Object.assign(task, { status: args.update.kind, sequence: args.sequence });
                return { applied: true, task: { ...task } };
            },
        } },
    };
    return { session, calls, tasks, ctx: { runId: "run-1", signal: controller.signal }, controller };
}

test("registers real activity before execution and keeps agent-free waits active without heartbeat traffic", async () => {
    const f = fixture();
    let release;
    const waiting = new Promise((resolve) => { release = resolve; });
    const result = { deliveryStatus: "waiting-for-ci" };
    const execution = withWorkflowActivity(f.session, "code-feature", f.ctx, async () => {
        assert.equal(f.tasks.get("task-0").status, "running");
        await waiting;
        return result;
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(f.calls.map(([name]) => name), ["register"]);
    assert.equal(f.calls[0][1].cancellable, false);
    assert.equal(f.tasks.get("task-0").status, "running");
    release();
    assert.equal(await execution, result);
    assert.equal(f.tasks.get("task-0").status, "completed");
    assert.deepEqual(f.calls[1][1].update.result, { runId: "run-1", workflowName: "code-feature" });
    assert.match(f.calls[1][1].update.message, /verification, CI and human-review gates/);
});

test("same-run resumed execution registers a separate activity without reviving terminal tasks", async () => {
    const f = fixture();
    await withWorkflowActivity(f.session, "pr-observer", f.ctx, async () => null);
    await withWorkflowActivity(f.session, "pr-observer", f.ctx, async () => null);
    const keys = f.calls.filter(([name]) => name === "register").map(([, args]) => args.clientTaskId);
    assert.equal(new Set(keys).size, 2);
    assert.ok(keys.every((key) => key.startsWith("workflow:run-1:")));
    assert.ok([...f.tasks.values()].every((task) => task.status === "completed"));
});

test("body errors close activity as failed without changing the original error", async () => {
    const f = fixture();
    const error = new Error("Source validation failed");
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, async () => { throw error; }), (actual) => actual === error);
    assert.equal(f.tasks.get("task-0").status, "failed");
    assert.equal(f.calls[1][1].update.error, error.message);
});

test("pause/cancel abort closes activity and propagates the original cooperative signal", async () => {
    const f = fixture();
    const error = new DOMException("Workflow run was aborted", "AbortError");
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, async () => {
        f.controller.abort(error);
        throw error;
    }), (actual) => actual === error);
    assert.equal(f.tasks.get("task-0").status, "cancelled");
    assert.match(f.calls[1][1].update.reason, /pause, cancellation and disconnection/);
});

test("an already aborted execution registers no task", async () => {
    const f = fixture();
    f.controller.abort();
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, () => assert.fail("must not run")), { name: "AbortError" });
    assert.deepEqual(f.calls, []);
});

test("abort racing registration closes the task before entering the body", async () => {
    const f = fixture();
    const register = f.session.rpc.tasks.register;
    f.session.rpc.tasks.register = async (args) => {
        const result = await register(args);
        f.controller.abort();
        return result;
    };
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, () => assert.fail("must not run")), { name: "AbortError" });
    assert.equal(f.tasks.get("task-0").status, "cancelled");
});

test("a body that returns after cancellation cannot report successful activity", async () => {
    const f = fixture();
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, async () => { f.controller.abort(); return {}; }), { name: "AbortError" });
    assert.equal(f.tasks.get("task-0").status, "cancelled");
});

test("unsupported or rejected registration fails before starting workflow work", async () => {
    await assert.rejects(withWorkflowActivity({}, "code-feature", {}, () => assert.fail("must not run")), /lacks client-owned background tasks/);
    const f = fixture();
    f.session.rpc.tasks.register = async () => { throw new Error("Method not found"); };
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, () => assert.fail("must not run")), /body was not started/);
    assert.equal(f.calls.length, 0);
});

test("invalid registration receipts do not start an untracked workflow", async () => {
    const f = fixture();
    for (const task of [null, {}, { id: "task", sequence: 0, status: "completed" }]) {
        f.session.rpc.tasks.register = async () => ({ task });
        await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, () => assert.fail("must not run")), /invalid activity receipt/);
    }
});

test("terminal update failure cannot produce a success-shaped handoff", async () => {
    const f = fixture();
    f.session.rpc.tasks.update = async () => { throw new Error("connection failed"); };
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, async () => null), /Could not close workflow background activity/);
});

test("unconfirmed terminal updates are explicit failures", async () => {
    const f = fixture();
    f.session.rpc.tasks.update = async () => ({ task: { id: "task-0", status: "running" } });
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, async () => null), /did not confirm the terminal/);
});

test("cleanup failure preserves pause semantics and reports the activity leak", async (t) => {
    const f = fixture();
    const errors = [];
    t.mock.method(console, "error", (message) => errors.push(message));
    f.session.rpc.tasks.update = async () => { throw new Error("disconnected"); };
    const error = new DOMException("paused", "AbortError");
    await assert.rejects(withWorkflowActivity(f.session, "code-feature", f.ctx, async () => { throw error; }), (actual) => actual === error);
    assert.match(errors[0], /runtime activity may be stale/);
    assert.equal(f.calls.at(-1)[0], "log");
});
