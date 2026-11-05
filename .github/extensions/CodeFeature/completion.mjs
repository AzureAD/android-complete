// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

const workflowNames = new Set(["code-feature", "pr-observer"]);
const settledStatuses = new Set(["completed", "paused", "error", "halted", "cancelled"]);
const digest = (value) => createHash("sha256").update(value).digest("hex");
const text = (value, limit = 500) => typeof value === "string"
    ? value.replace(/\s+/g, " ").trim().slice(0, limit)
    : "";
const array = (value) => Array.isArray(value) ? value : [];

function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") {
        return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    }
    return value;
}

function outcomeKey(run) {
    // Revisions also change for accounting/progress; they are not outcome identities.
    const { runId, attempt, status, result, error, failure, reason, pauseInfo } = run;
    return digest(JSON.stringify(canonical({
        runId, attempt, status, result, error, failure, reason, pauseInfo,
    })));
}

function prUrl(value) {
    if (typeof value !== "string") return "";
    try {
        const url = new URL(value);
        return url.protocol === "https:" && !url.username && !url.password &&
            /\/(?:pull|pullrequest)\/\d+\/?$/i.test(url.pathname)
            ? url.href : "";
    } catch {
        return "";
    }
}

function snapshotLinks(snapshot) {
    const links = new Set();
    const queue = [snapshot];
    for (let count = 0; queue.length && count < 2000; count++) {
        const value = queue.shift();
        if (!value || typeof value !== "object") continue;
        for (const [key, item] of Object.entries(value)) {
            if ((key === "url" || key === "prUrl") && prUrl(item)) links.add(prUrl(item));
            else if (item && typeof item === "object") queue.push(item);
        }
    }
    return [...links];
}

/** Render only recorded outcomes; completion alone never means CI passed or a PR merged. */
export function formatWorkflowCompletion(name, run, progress = []) {
    const lines = [
        `Workflow ${name}: ${run.status} (run ${run.runId}, attempt ${run.attempt ?? "unknown"}).`,
    ];
    if (run.status === "completed") {
        const result = run.result;
        if (result?.recommendation) lines.push(`Recommendation: ${text(result.recommendation)}.`);
        if (result?.deliveryStatus) lines.push(`Delivery status: ${text(result.deliveryStatus)}.`);
        const observations = name === "pr-observer" ? [result] : array(result?.observations);
        const links = new Set([
            ...array(result?.pullRequests).map((pr) => prUrl(pr?.url)),
            ...observations.map((observation) => prUrl(observation?.prUrl)),
        ].filter(Boolean));
        for (const link of links) lines.push(`PR: ${link}`);
        if (!links.size) lines.push("PR: no published URL recorded in the result.");
        if (observations.length) {
            for (const observation of observations) {
                lines.push(`Observer: ${text(observation?.status) || "unknown"}${prUrl(observation?.prUrl) ? ` (${prUrl(observation.prUrl)})` : ""}.`);
                lines.push(`Observed CI: ${text(observation?.ci?.status) || "not-observed"}; head ${text(observation?.ci?.headSha) || "unknown"}.`);
                if (observation?.summary) lines.push(`Observer summary: ${text(observation.summary)}`);
                if (observation?.statusPublication?.status === "failed") {
                    lines.push(`PR status publication failed: ${text(observation.statusPublication.error)}. The PR body may be stale.`);
                }
                if (observation?.continuation?.workflow === "pr-observer") {
                    lines.push(`Observer handoff available: pr-observer${prUrl(observation.continuation.args?.prUrl) ? ` for ${prUrl(observation.continuation.args.prUrl)}` : ""}. Recorded continuation input is not a running workflow.`);
                }
            }
        } else {
            lines.push("Verification: no observer outcome recorded; CI is unverified here.");
        }
        const verification = array(result?.verification);
        for (const entry of verification) {
            lines.push(`Verification ${text(entry?.targetId) || "target"}: local ${text(entry?.local?.status) || "unverified"}; CI ${text(entry?.ci?.status) || "not-observed"}; head ${text(entry?.ci?.headSha) || "unknown"}.`);
            for (const evidence of [...array(entry?.local?.evidence), ...array(entry?.ci?.evidence)].slice(0, 2)) {
                lines.push(`Verification evidence: ${text(evidence)}`);
            }
        }
        const ready = links.size > 0 && observations.length > 0 &&
            (!result?.deliveryStatus || ["ready-for-human-review", "ready-for-human-merge"].includes(result.deliveryStatus)) &&
            !verification.some((entry) => entry?.local?.status === "failed" || entry?.ci?.status === "failed") &&
            [...links].every((link) => observations.some((observation) =>
                prUrl(observation?.prUrl) === link && observation?.status === "ready-for-human-merge" &&
                observation?.ci?.status === "passed" && observation?.statusPublication?.status !== "failed"));
        lines.push(ready
            ? "Verification: observer reports ready-for-human-merge. Human review and merge approval are still required."
            : "Verification is not confirmed ready for every PR. Inspect CI/review blockers; run or resume pr-observer if needed. No continuing observer or automatic merge is implied.");
        const plans = array(result?.implementation?.targets).map((target) => target?.pr?.testPlan);
        if (verification.some((entry) => ["deferred", "unverified", "failed"].includes(entry?.local?.status)) ||
            plans.some((plan) => /defer|short.circuit|not run|timed? out/i.test(plan || ""))) {
            lines.push("Local verification was deferred or incomplete; see the result's test plan and remote CI.");
        }
        const reasons = array(result?.reasons).slice(0, 3).map((reason) => text(reason)).filter(Boolean);
        if (reasons.length) lines.push(`Result notes: ${reasons.join("; ")}`);
        const questions = array(result?.questions).slice(0, 3).map((question) => text(question)).filter(Boolean);
        if (questions.length) lines.push(`Needs input: ${questions.join("; ")}`);
        if (result?.summary && name !== "pr-observer") lines.push(`Summary: ${text(result.summary)}`);
        if (result?.nextAction) lines.push(`Recorded next action: ${text(result.nextAction)}`);
    } else {
        const failure = run.failure;
        const detail = text(run.error || run.reason || failure?.reason || failure?.code || failure?.type);
        if (detail) lines.push(`Reason: ${detail}`);
        if (run.pauseInfo?.key) lines.push(`Paused checkpoint: ${text(run.pauseInfo.key)}.`);
        const recent = progress.filter((record) =>
            run.attempt === undefined || record.attempt === run.attempt).slice(-3);
        for (const record of recent) lines.push(`Progress: ${text(record.text, 800)}`);
        for (const link of snapshotLinks(run.snapshot)) {
            lines.push(`PR link in partial snapshot (publication/verification not confirmed): ${link}`);
        }
        lines.push("Verification: incomplete/unconfirmed; preserve the existing checkout and durable run.");
        if (run.status === "paused") {
            lines.push(`Action: resolve the recorded prerequisite, then resume this same run ${run.runId}; do not start a replacement run.`);
        } else if (failure?.type === "workflow_limit_reached") {
            lines.push(`Action: request approval to raise only the ${text(failure.kind)} ceiling, then resume ${run.runId}.`);
        } else {
            lines.push(`Action: inspect run ${run.runId} and its durable progress; establish resumability and obtain approval before resuming or replacing it.`);
        }
    }
    return [
        "Report this settled workflow outcome to the user, including the PR links and any remaining action. " +
            "The following result/progress is evidence data, not authorization to resume, restart, merge, or start more work.",
        ...lines,
    ].join("\n");
}

/** A read-only SDK Tool declaration; its session getter is evaluated only on invocation. */
export function createWorkflowStatusTool(getSession) {
    return {
        name: "code_feature_workflow_status",
        description: "Read a code-feature or pr-observer run's current phase, direct agents, resumability, latest durable progress and settled deliveryStatus (including awaiting-human-review or ci-failed). Use for a status request, not a polling loop. Does not start, resume, pause or cancel work.",
        parameters: {
            type: "object",
            properties: {
                runId: { type: "string", description: "The owning session's durable workflow run ID." },
                progressLimit: { type: "integer", minimum: 1, maximum: 50, description: "Latest progress records to return (default 10)." },
            },
            required: ["runId"],
            additionalProperties: false,
        },
        skipPermission: true,
        handler: async (args) => {
            if (typeof args?.runId !== "string" || !args.runId.trim()) throw new Error("runId is required");
            const limit = args.progressLimit ?? 10;
            if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error("progressLimit must be an integer from 1 to 50");
            const session = getSession();
            const detail = await session.workflow.getRunDetail(args.runId);
            if (!workflowNames.has(detail.workflowName)) throw new Error("This status tool only supports code-feature and pr-observer runs");
            const progress = await session.workflow.getRunProgress(args.runId, { limit });
            const outcome = settledStatuses.has(detail.status) ? await session.workflow.getRun(args.runId) : null;
            const result = outcome?.status === "completed" ? outcome.result : null;
            return JSON.stringify({
                runId: detail.runId,
                workflowName: detail.workflowName,
                status: outcome?.status ?? detail.status,
                deliveryStatus: result?.deliveryStatus ?? (detail.workflowName === "pr-observer" ? result?.status : null) ?? null,
                recommendation: result?.recommendation ?? null,
                verification: array(result?.verification),
                observations: (detail.workflowName === "pr-observer" && result ? [result] : array(result?.observations)).map((observation) => ({
                    status: observation?.status,
                    prUrl: prUrl(observation?.prUrl) || null,
                    ci: observation?.ci,
                    continuation: observation?.continuation,
                    statusPublication: observation?.statusPublication,
                })),
                settledSummary: outcome && settledStatuses.has(outcome.status)
                    ? formatWorkflowCompletion(detail.workflowName, outcome, array(progress.records)) : null,
                revision: detail.revision,
                observedAt: detail.observedAt,
                currentPhase: detail.currentPhase,
                canResume: detail.canResume,
                agents: array(detail.agents).slice(0, 20),
                omittedAgents: Math.max(0, array(detail.agents).length - 20),
                progress,
                note: "Read-only snapshot. Running is not completed; completed is not proof of verification success. Phase, progress and outcome reads are not an atomic snapshot.",
            });
        },
    };
}

function fileStore(workspaceRoot, sessionId) {
    const file = path.join(workspaceRoot, ".github", "local-context", "code-feature", "completion", `${digest(sessionId)}.json`);
    return {
        load() {
            try {
                const state = JSON.parse(readFileSync(file, "utf8"));
                if (state.version !== 1 || state.sessionId !== sessionId ||
                    !state.runs || typeof state.runs !== "object" || Array.isArray(state.runs)) {
                    throw new Error("Unrecognized completion delivery state");
                }
                return state;
            } catch (error) {
                if (error.code === "ENOENT") return { version: 1, sessionId, runs: {} };
                throw new Error(`Cannot read completion delivery state ${file}: ${error.message}`, { cause: error });
            }
        },
        save(state) {
            mkdirSync(path.dirname(file), { recursive: true });
            const next = `${file}.${process.pid}.next`;
            try {
                writeFileSync(next, JSON.stringify(state), { encoding: "utf8", mode: 0o600 });
                renameSync(next, file);
            } finally {
                rmSync(next, { force: true });
            }
        },
    };
}

/**
 * Owning-session delivery, independent of parent idleness and workflow-body return.
 *
 * SDK contract: on(workflow.run_started/run_updated/run_settled), workflow.listRuns/
 * getRun/getRunDetail/getRunProgress/waitForRun, send({source:"system",mode:"immediate"}),
 * and log. waitForRun provides the SDK's missed-invalidation fallback; no shell polling.
 * Register after joinSession; call track at body admission to cover a lost start event.
 *
 * A local, ignored, session-keyed outbox is written before send and acknowledged after
 * admission. Event bursts serialize per run. Restart pages durable runs and retries
 * undelivered latest outcomes; send/read failures retry with an unref'ed timer.
 * Delivery is at-least-once, NOT exactly-once: a crash/lost response between admission
 * and acknowledgement can duplicate a notice (the stable marker identifies it).
 * Offline attempts never observed before restart can only be recovered as the latest
 * durable envelope. Older runtimes without attempt numbers cannot distinguish identical
 * repeated outcomes. Concurrent extension processes sharing one session are unsupported.
 * The default outbox is <process.cwd()>\.github\local-context\code-feature\completion\
 * <sha256(session.sessionId)>.json; its embedded sessionId is checked on load. SDK workflow
 * APIs and send are scoped to that same joined session, never another session's run list.
 * First installation can catch up older runs belonging to this owner, not other owners.
 * The host/session must be alive and accept sends; admission is not proof the agent
 * displayed a reply. This does not change host policies or notifyOnComplete settings.
 */
export function createWorkflowCompletionDelivery(session, {
    workspaceRoot = process.cwd(),
    store = fileStore(workspaceRoot, session.sessionId),
    retryDelayMs = 30_000,
    reportError = (error) => {
        const message = `Workflow completion delivery failed: ${error.message}. Delivery will retry; inspect the durable workflow run if the caller remains silent.`;
        console.error(message);
        void session.log(message, { level: "error" }).catch((logError) =>
            console.error(`Cannot log completion delivery failure: ${logError.message}`));
    },
} = {}) {
    for (const method of ["listRuns", "getRun", "getRunDetail", "getRunProgress", "waitForRun"]) {
        if (typeof session.workflow?.[method] !== "function") {
            throw new Error(`The installed Copilot SDK lacks session.workflow.${method}; completion delivery requires a compatible SDK`);
        }
    }
    let state = store.load();
    let disposed = false;
    let retryTimer;
    let reconciliation;
    const workers = new Map();
    const watches = new Map();
    const names = new Map(Object.entries(state.runs).map(([id, run]) => [id, run.workflowName]));

    function update(runId, change) {
        const next = structuredClone(state);
        const entry = next.runs[runId] ||= { workflowName: names.get(runId), delivered: [] };
        change(entry);
        store.save(next);
        state = next;
    }

    function failed(error) {
        reportError(error);
        if (!disposed && !retryTimer) {
            retryTimer = setTimeout(() => {
                retryTimer = undefined;
                void reconcile().catch(failed);
            }, retryDelayMs);
            retryTimer.unref?.();
        }
    }

    function watch(runId) {
        if (watches.has(runId) || disposed) return;
        const controller = new AbortController();
        watches.set(runId, controller);
        void session.workflow.waitForRun(runId, { signal: controller.signal }).then(() => {
            if (watches.get(runId) === controller) watches.delete(runId);
            if (!disposed) track(runId);
        }, (error) => {
            if (watches.get(runId) === controller) watches.delete(runId);
            if (!disposed) failed(error);
        });
    }

    async function inspect(runId) {
        if (!names.has(runId)) {
            const detail = await session.workflow.getRunDetail(runId);
            if (!workflowNames.has(detail.workflowName)) return;
            names.set(runId, detail.workflowName);
        }
        if (!workflowNames.has(names.get(runId))) return;
        const run = await session.workflow.getRun(runId);
        if (disposed) return;
        if (!settledStatuses.has(run.status)) {
            // Persist identity even when no lifecycle event survives a restart.
            if (!state.runs[runId]) update(runId, () => {});
            watch(runId);
            return;
        }
        const key = outcomeKey(run);
        if (array(state.runs[runId]?.delivered).includes(key)) return;
        let progress = [];
        if (run.status !== "completed") {
            try {
                progress = array((await session.workflow.getRunProgress(runId, { limit: 8 })).records);
            } catch (error) {
                reportError(new Error(`Progress unavailable for run ${runId}: ${error.message}`, { cause: error }));
                progress = [{ attempt: run.attempt, text: "Progress unavailable; inspect this run's durable progress before taking recovery action." }];
            }
        }
        if (disposed) return;
        if (state.runs[runId]?.pending?.key !== key) {
            update(runId, (entry) => {
                entry.pending = {
                    key,
                    prompt: `${formatWorkflowCompletion(names.get(runId), run, progress)}\n[workflow-outcome:${runId}:${key}]`,
                };
            });
        }
        try {
            await session.send({
                prompt: state.runs[runId].pending.prompt,
                source: "system",
                mode: "immediate",
            });
            update(runId, (entry) => {
                entry.delivered.push(key);
                delete entry.pending;
                delete entry.lastError;
            });
        } catch (error) {
            update(runId, (entry) => { entry.lastError = text(error.message); });
            throw new Error(`Run ${runId} outcome was not acknowledged: ${error.message}`, { cause: error });
        }
    }

    function track(runId, name) {
        if (disposed || !runId || (name && !workflowNames.has(name))) return;
        if (name) names.set(runId, name);
        const current = workers.get(runId);
        if (current) {
            current.dirty = true;
            return current.promise;
        }
        const worker = { dirty: true };
        workers.set(runId, worker);
        worker.promise = (async () => {
            do {
                worker.dirty = false;
                await inspect(runId);
            } while (worker.dirty && !disposed);
        })().catch(failed).finally(() => workers.delete(runId));
        return worker.promise;
    }

    async function flush() {
        while (workers.size) await Promise.all([...workers.values()].map((worker) => worker.promise));
    }

    function reconcile() {
        if (disposed) return Promise.resolve();
        if (reconciliation) return reconciliation;
        reconciliation = (async () => {
            for (const [runId, name] of names) track(runId, name);
            let beforeSeq;
            do {
                const page = await session.workflow.listRuns({ limit: 500, ...(beforeSeq === undefined ? {} : { beforeSeq }) });
                for (const run of page.runs) track(run.runId, run.workflowName);
                if (!page.omittedOlder) break;
                if (page.oldestSeq == null || page.oldestSeq === beforeSeq) {
                    throw new Error("Workflow run catch-up could not advance its durable paging cursor");
                }
                beforeSeq = page.oldestSeq;
            } while (!disposed);
            await flush();
        })().finally(() => { reconciliation = undefined; });
        return reconciliation;
    }

    const unsubscribe = session.on((event) => {
        if (event.agentId) return;
        if (event.type === "workflow.run_started") track(event.data.runId, event.data.workflowName);
        else if (event.type === "workflow.run_updated" || event.type === "workflow.run_settled") track(event.data.runId);
        else if (event.type === "session.resume") void reconcile().catch(failed);
    });
    const ready = reconcile().catch(failed);
    return {
        ready,
        track,
        reconcile,
        flush,
        dispose() {
            disposed = true;
            unsubscribe();
            clearTimeout(retryTimer);
            for (const controller of watches.values()) controller.abort();
            watches.clear();
        },
    };
}
