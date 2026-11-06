// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { randomUUID } from "node:crypto";

/**
 * Represent an executing workflow body as real, client-owned background work.
 * A chat-idle notification must not make agent-free CI waits eligible for cleanup.
 */
export async function withWorkflowActivity(session, workflowName, ctx, execute) {
    const tasks = session.rpc?.tasks;
    if (typeof tasks?.register !== "function" || typeof tasks?.update !== "function") {
        throw new Error("This runtime lacks client-owned background tasks. Update the Copilot app/SDK before running this workflow; idle-session cleanup could otherwise interrupt it.");
    }
    ctx.signal?.throwIfAborted();

    let registration;
    try {
        registration = await tasks.register({
            type: "client",
            clientTaskId: `workflow:${ctx.runId}:${randomUUID()}`,
            displayName: `${workflowName} execution`,
            description: `Executing workflow ${ctx.runId}, including build and CI waits. Use the workflow controls to cancel. Completion of this activity is not proof of tests passing.`,
            // Cancellation belongs to the workflow, whose signal unwinds this scope.
            cancellable: false,
        });
    } catch (error) {
        throw new Error(`Cannot register workflow background activity: ${error.message}. The workflow body was not started; continuing without runtime tracking would risk idle cleanup.`, { cause: error });
    }
    const task = registration?.task;
    if (!task || typeof task.id !== "string" || !task.id ||
        !Number.isSafeInteger(task.sequence) || task.sequence < 0 || task.status !== "running") {
        throw new Error("Workflow background-task registration returned an invalid activity receipt; the workflow body was not started.");
    }

    let bodyFailed = false;
    let update;
    try {
        ctx.signal?.throwIfAborted();
        const result = await execute();
        ctx.signal?.throwIfAborted();
        update = {
            kind: "completed",
            message: "Workflow execution scope ended. Consult the workflow result for verification, CI and human-review gates.",
            result: { runId: ctx.runId, workflowName },
        };
        return result;
    } catch (error) {
        bodyFailed = true;
        update = ctx.signal?.aborted || error?.name === "AbortError"
            ? { kind: "cancelled", reason: "Workflow execution stopped. Consult its durable state to distinguish pause, cancellation and disconnection." }
            : { kind: "failed", error: error?.message || String(error) };
        throw error;
    } finally {
        try {
            const receipt = await tasks.update({ id: task.id, sequence: task.sequence + 1, update });
            if (receipt?.task?.id !== task.id || receipt.task.status !== update.kind) {
                throw new Error("Runtime did not confirm the terminal background-task update.");
            }
        } catch (error) {
            const failure = new Error(`Could not close workflow background activity ${task.id}: ${error.message}. Its runtime activity may be stale; inspect the workflow before recovery.`, { cause: error });
            if (!bodyFailed) throw failure;
            // Keep the original pause/cancellation/error semantics when cleanup also fails.
            console.error(failure.message);
            try { await session.log(failure.message, { level: "error" }); }
            catch (logError) { console.error(`Cannot report background-activity cleanup failure: ${logError.message}`); }
        }
    }
}
