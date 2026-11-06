// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { createHash } from "node:crypto";

const names = new Set(["code-feature", "pr-observer"]);
const settled = new Set(["completed", "paused", "error", "halted", "cancelled"]);

/**
 * An attached tool call supervises real workflow execution while its chat would
 * otherwise be idle. No workflow controls, dummy work or heartbeat messages.
 */
export function createWorkflowSupervision(getSession, { waitMs = 300_000 } = {}) {
    if (!Number.isSafeInteger(waitMs) || waitMs <= 0) throw new Error("waitMs must be a positive integer");
    const listeners = new Map();
    return {
        notifyMilestone(runId, milestone) {
            for (const notify of listeners.get(runId) || []) notify(milestone);
        },
        tool: {
            name: "code_feature_workflow_wait",
            description: "Supervise an already-started code-feature or pr-observer run in an attached, event-driven wait. Returns at a new milestone, settlement, or a five-minute observation boundary. If still running, continue waiting on the SAME run after reporting new progress. Does not start, resume, pause, cancel, requeue or change budgets. Use the status tool for one-off status requests.",
            parameters: {
                type: "object",
                properties: { runId: { type: "string", description: "The existing run ID in this owning session." } },
                required: ["runId"],
                additionalProperties: false,
            },
            skipPermission: true,
            handler: async (args, invocation = {}) => {
                if (typeof args?.runId !== "string" || !args.runId.trim()) throw new Error("runId is required");
                if (invocation.agentId) throw new Error("Workflow supervision belongs to the owning caller, not a workflow subagent.");
                invocation.signal?.throwIfAborted();
                const session = getSession();
                if (typeof session.workflow?.waitForRun !== "function") throw new Error("The installed SDK does not support attached workflow observation.");
                const runId = args.runId;
                const controller = new AbortController();
                let timer;
                let notify;
                const milestone = new Promise((resolve) => { notify = (value) => resolve({ kind: "milestone", milestone: value }); });
                const subscriptions = listeners.get(runId) || new Set();
                subscriptions.add(notify);
                listeners.set(runId, subscriptions);
                const abort = () => controller.abort(invocation.signal.reason);
                invocation.signal?.addEventListener("abort", abort, { once: true });
                try {
                    invocation.signal?.throwIfAborted();
                    const detail = await session.workflow.getRunDetail(runId);
                    if (!names.has(detail.workflowName)) throw new Error("Only code-feature and pr-observer runs can be supervised.");
                    invocation.signal?.throwIfAborted();
                    if (settled.has(detail.status)) {
                        return { kind: "settled", run: await session.workflow.getRun(runId), nextAction: "Inspect this actual outcome; completion is not proof of CI success. Do not automatically resume or restart." };
                    }
                    const result = await Promise.race([
                        session.workflow.waitForRun(runId, { signal: controller.signal }).then((run) => ({ kind: "settled", run })),
                        milestone,
                        new Promise((resolve) => { timer = setTimeout(() => resolve({ kind: "observation-window-ended" }), waitMs); }),
                    ]);
                    invocation.signal?.throwIfAborted();
                    if (result.kind === "settled") {
                        return { ...result, nextAction: "Inspect this actual outcome; completion is not proof of CI success. Do not automatically resume or restart." };
                    }
                    const current = await session.workflow.getRunDetail(runId);
                    invocation.signal?.throwIfAborted();
                    if (settled.has(current.status)) {
                        return { kind: "settled", run: await session.workflow.getRun(runId), nextAction: "Inspect the actual settled result and any required input; do not automatically resume or restart." };
                    }
                    return {
                        ...result, runId, status: current.status,
                        currentPhase: current.currentPhase ?? null,
                        ...(result.kind === "milestone" ? {
                            marker: `[workflow-milestone:${runId}:${createHash("sha256").update(result.milestone.id).digest("hex")}]`,
                        } : {}),
                        note: "Work is not complete. This wait performed no workflow or CI mutations. A milestone is recorded progress, not proof the next phase or verification completed.",
                        nextAction: `Relay only new progress; deduplicate the marker against system milestone messages. Then call code_feature_workflow_wait with the same runId ${runId}. Do not end supervision with task_complete while the authorized run is still running. If the user interrupted or withdrew the request, respect that instead.`,
                    };
                } finally {
                    clearTimeout(timer);
                    invocation.signal?.removeEventListener("abort", abort);
                    controller.abort();
                    subscriptions.delete(notify);
                    if (!subscriptions.size) listeners.delete(runId);
                }
            },
        },
    };
}
