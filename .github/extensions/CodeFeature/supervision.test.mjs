// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import assert from "node:assert/strict";
import test from "node:test";
import { createWorkflowSupervision } from "./supervision.mjs";

function fixture() {
    let settle;
    const calls = [];
    const result = { runId: "run-1", status: "completed", result: { deliveryStatus: "waiting-for-ci" } };
    const detail = { runId: "run-1", workflowName: "code-feature", status: "running", currentPhase: { title: "Observe" } };
    const session = { workflow: {
        getRunDetail: async () => { calls.push("detail"); return { ...detail }; },
        getRun: async () => { calls.push("run"); return result; },
        waitForRun: (_id, { signal }) => new Promise((resolve, reject) => {
            calls.push("wait");
            settle = resolve;
            signal.addEventListener("abort", () => { calls.push("abort-wait"); reject(signal.reason); }, { once: true });
        }),
    } };
    return { session, detail, result, calls, settle: (run = result) => settle(run) };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("a quiet running workflow is supervised by one held wait, not repeated status polls", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    let returned = false;
    const wait = supervision.tool.handler({ runId: "run-1" }).then((result) => { returned = true; return result; });
    await tick();
    assert.equal(returned, false);
    assert.deepEqual(f.calls, ["detail", "wait"]);
    f.settle();
    const outcome = await wait;
    assert.equal(outcome.kind, "settled");
    assert.equal(outcome.run, f.result);
    assert.equal(outcome.run.result.deliveryStatus, "waiting-for-ci");
    assert.match(outcome.nextAction, /not proof of CI success/);
});

test("direct producer milestones yield promptly without workflow invalidation events", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    const wait = supervision.tool.handler({ runId: "run-1" });
    await tick();
    supervision.notifyMilestone("another-run", { id: "unrelated" });
    const milestone = { id: "ci-start", title: "CI observation", outcome: "deferred" };
    supervision.notifyMilestone("run-1", milestone);
    const result = await wait;
    assert.equal(result.kind, "milestone");
    assert.equal(result.milestone, milestone);
    assert.match(result.marker, /^\[workflow-milestone:run-1:[a-f0-9]{64}\]$/);
    assert.match(result.nextAction, /same runId run-1/);
    assert.ok(f.calls.includes("abort-wait"));
});

test("subscription precedes status lookup so a racing milestone is not lost", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    const get = f.session.workflow.getRunDetail;
    f.session.workflow.getRunDetail = async () => {
        supervision.notifyMilestone("run-1", { id: "race" });
        return get();
    };
    assert.equal((await supervision.tool.handler({ runId: "run-1" })).milestone.id, "race");
});

test("observation boundaries return current state and continuation, not completion", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session, { waitMs: 5 });
    const result = await supervision.tool.handler({ runId: "run-1" });
    assert.equal(result.kind, "observation-window-ended");
    assert.equal(result.status, "running");
    assert.match(result.nextAction, /Do not end supervision with task_complete/);
    assert.deepEqual(f.calls, ["detail", "wait", "detail", "abort-wait"]);
});

test("settlement racing a milestone cannot be reported as still running", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    const wait = supervision.tool.handler({ runId: "run-1" });
    await tick();
    f.detail.status = "completed";
    supervision.notifyMilestone("run-1", { id: "last" });
    assert.equal((await wait).kind, "settled");
});

test("already settled paused and halted attempts return without resuming or waiting", async () => {
    for (const status of ["completed", "paused", "error", "halted", "cancelled"]) {
        const f = fixture();
        f.detail.status = status;
        f.result.status = status;
        const result = await createWorkflowSupervision(() => f.session).tool.handler({ runId: "run-1" });
        assert.equal(result.run.status, status);
        assert.deepEqual(f.calls, ["detail", "run"]);
    }
});

test("cancelling the caller wait cleans up without cancelling its workflow", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    const controller = new AbortController();
    const wait = supervision.tool.handler({ runId: "run-1" }, { signal: controller.signal });
    await tick();
    controller.abort();
    await assert.rejects(wait, { name: "AbortError" });
    assert.deepEqual(f.calls, ["detail", "wait", "abort-wait"]);
    supervision.notifyMilestone("run-1", { id: "after-cancel" });
});

test("abort during the initial detail read does not leak a watcher", async () => {
    const f = fixture();
    const controller = new AbortController();
    const get = f.session.workflow.getRunDetail;
    f.session.workflow.getRunDetail = async () => { controller.abort(); return get(); };
    await assert.rejects(createWorkflowSupervision(() => f.session).tool.handler({ runId: "run-1" }, { signal: controller.signal }), { name: "AbortError" });
    assert.deepEqual(f.calls, ["detail"]);
});

test("provider errors and unsupported runs are explicit instead of starting replacement work", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    await assert.rejects(supervision.tool.handler({}), /runId is required/);
    await assert.rejects(supervision.tool.handler({ runId: "run-1" }, { agentId: "worker" }), /owning caller/);
    f.detail.workflowName = "unrelated";
    await assert.rejects(supervision.tool.handler({ runId: "run-1" }), /Only code-feature/);
    f.detail.workflowName = "pr-observer";
    f.session.workflow.waitForRun = async () => { throw new Error("connection lost"); };
    await assert.rejects(supervision.tool.handler({ runId: "run-1" }), /connection lost/);
});

test("concurrent caller waits have independent cancellation and share no mutation state", async () => {
    const f = fixture();
    const supervision = createWorkflowSupervision(() => f.session);
    const controller = new AbortController();
    const one = supervision.tool.handler({ runId: "run-1" }, { signal: controller.signal });
    const two = supervision.tool.handler({ runId: "run-1" });
    await tick();
    controller.abort();
    await assert.rejects(one, { name: "AbortError" });
    supervision.notifyMilestone("run-1", { id: "live" });
    assert.equal((await two).milestone.id, "live");
});
