// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { createHash } from "node:crypto";
import { appendFileSync, copyFileSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { rename } from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { formatWorkflowMilestone, humanInputRequestFromProgress, milestoneFromProgress, normalizeMilestone } from "./milestones.mjs";
import { deliveryStatusSummary, observerNextAction } from "./status.mjs";
import { ciVerificationSummary, localVerificationSummary } from "./verification.mjs";

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
    const lines = [];
    if (run.status === "completed") {
        const result = run.result;
        if (result?.deliveryStatus || result?.recommendation) {
            lines.push(deliveryStatusSummary(result.deliveryStatus || result.recommendation));
        } else {
            lines.push(name === "pr-observer" ? "PR observation finished." : "Workflow finished; inspect the recorded results.");
        }
        const observations = name === "pr-observer" ? [result] : array(result?.observations);
        const links = new Set([
            ...array(result?.pullRequests).map((pr) => prUrl(pr?.url)),
            ...observations.map((observation) => prUrl(observation?.prUrl)),
        ].filter(Boolean));
        for (const link of links) lines.push(`PR: ${link}`);
        if (!links.size) lines.push("PR: no published URL recorded in the result.");
        if (observations.length) {
            for (const observation of observations) {
                if (observations.length > 1) lines.push(`PR observation: ${prUrl(observation?.prUrl) || "PR URL unavailable"}.`);
                lines.push(text(observation?.summary) || deliveryStatusSummary(observation?.status));
                lines.push(`${ciVerificationSummary(observation)} Observed head: ${text(observation?.ci?.headSha) || "unavailable"}.`);
                const findings = array(observation?.actionableFindings);
                for (const finding of findings.slice(0, 2)) lines.push(`Remaining finding: ${text(finding)}`);
                if (!findings.length && observation?.ci?.status !== "passed") {
                    for (const evidence of array(observation?.evidence).slice(0, 2)) lines.push(`PR check evidence: ${text(evidence)}`);
                }
                if (observation?.status !== "ready-for-human-merge") lines.push(`Next: ${observerNextAction(observation || {})}`);
                else if (observation?.statusPublication?.status === "failed") lines.push(`Next: ${observerNextAction(observation)}`);
                if (observation?.statusPublication?.status === "failed") {
                    lines.push(`PR status publication failed: ${text(observation.statusPublication.error)}. The PR body may be stale.`);
                }
                if (observation?.continuation?.workflow === "pr-observer") {
                    lines.push("A continuation is available for this PR; this handoff is not a running observer.");
                }
            }
        } else {
            lines.push("No PR observation was recorded; CI has not been confirmed.");
        }
        const verification = array(result?.verification);
        for (const entry of verification) {
            lines.push(`${text(entry?.targetId) || "Target"}: ${localVerificationSummary(entry)}`);
            if (!observations.some((observation) => observation?.ci?.headSha && observation.ci.headSha === entry?.ci?.headSha &&
                observation.ci.status === entry?.ci?.status)) {
                lines.push(`${ciVerificationSummary(entry)} Observed head: ${text(entry?.ci?.headSha) || "unavailable"}.`);
            }
            for (const evidence of [...array(entry?.local?.evidence), ...array(entry?.ci?.evidence)].slice(0, 2)) {
                if (!localVerificationSummary(entry).includes(evidence)) lines.push(`Verification evidence: ${text(evidence)}`);
            }
        }
        const ready = links.size > 0 && observations.length > 0 &&
            (!result?.recommendation || !["NO-GO", "needs clarification"].includes(result.recommendation)) &&
            (!result?.deliveryStatus || ["ready-for-human-review", "ready-for-human-merge"].includes(result.deliveryStatus)) &&
            !verification.some((entry) => entry?.local?.status === "failed" || entry?.ci?.status === "failed") &&
            [...links].every((link) => observations.some((observation) =>
                prUrl(observation?.prUrl) === link && observation?.status === "ready-for-human-merge" &&
                observation?.ci?.status === "passed" && typeof observation.ci.headSha === "string" && observation.ci.headSha.trim() &&
                observation?.statusPublication?.status !== "failed"));
        lines.push(ready
            ? "CI and observed PR checks passed for every published PR. Human review and merge approval are still required."
            : "Merge readiness is not confirmed for every PR.");
        const plans = array(result?.implementation?.targets).map((target) => target?.pr?.testPlan);
        if (!verification.length &&
            plans.some((plan) => /defer|short.circuit|not run|timed? out/i.test(plan || ""))) {
            lines.push("Local verification is incomplete; see the test plan for checks left to CI.");
        }
        const reasons = array(result?.reasons).slice(0, 3).map((reason) => text(reason)).filter(Boolean);
        if (reasons.length) lines.push(`Result notes: ${reasons.join("; ")}`);
        const questions = array(result?.questions).slice(0, 3).map((question) => text(question)).filter(Boolean);
        if (questions.length) {
            lines.push(`Needs input: ${questions.join("; ")}`);
            lines.push("Open an actual ask_user question for the recorded missing detail, one question at a time; do not silently end with a clarification status. An answer is not authorization to restart a completed run or change the approved task scope.");
        }
        if (result?.summary && name !== "pr-observer") lines.push(`Summary: ${text(result.summary)}`);
        if (result?.nextAction) lines.push(`Next: ${text(result.nextAction)}`);
    } else {
        lines.push({
            paused: "Work is paused for a prerequisite or developer decision.",
            error: "The workflow stopped with an error.",
            halted: "The workflow was interrupted.",
            cancelled: "The workflow was cancelled.",
        }[run.status] || "The workflow stopped before completion.");
        const failure = run.failure;
        const detail = text(run.error || run.reason || failure?.reason || failure?.code || failure?.type);
        if (detail) lines.push(`Reason: ${detail}`);
        if (run.pauseInfo?.key) lines.push(`Paused checkpoint: ${text(run.pauseInfo.key)}.`);
        const recent = progress.filter((record) =>
            (run.attempt === undefined || record.attempt === run.attempt) &&
            (typeof record.text !== "string" || !record.text.startsWith("[workflow-input-request-v1]"))).slice(-3);
        for (const record of recent) lines.push(`Last recorded progress: ${milestoneFromProgress(record)?.summary || text(record.text, 800)}`);
        for (const link of snapshotLinks(run.snapshot)) {
            lines.push(`PR link in partial snapshot (publication/verification not confirmed): ${link}`);
        }
        lines.push("Verification is incomplete; preserve the existing checkout and run.");
        if (run.status === "paused") {
            const input = humanInputRequestFromProgress(progress.filter((record) =>
                run.attempt === undefined || record.attempt === run.attempt), run.pauseInfo?.key);
            lines.push("Human input is required: open an actionable ask_user question now; do not end with only a paused-status report. Ask one question at a time and preserve any existing input request instead of creating duplicates. If the user already answered this recorded question, apply that answer instead of asking again.");
            lines.push(`Confirm this same run is still paused before asking. After the answer, perform the documented recovery and resume this run only when the blocker is actually resolved. Do not treat a reply as a global environment change or approval to install software.`);
            if (input) {
                lines.push(`Recorded question data: ${JSON.stringify(input.questions)}.`);
                if (typeof input.recoveryPath === "string") lines.push(`Run-scoped recovery path: ${input.recoveryPath}`);
            } else {
                lines.push("Use the recorded blocker to ask for the specific missing decision or prerequisite; do not silently park the caller.");
            }
            lines.push(`Action: resolve the recorded prerequisite, then resume this same run ${run.runId}; do not start a replacement run.`);
        } else if (failure?.type === "workflow_limit_reached") {
            lines.push(`Action: request approval to raise only the ${text(failure.kind)} ceiling, then resume ${run.runId}.`);
        } else {
            lines.push(`Action: inspect run ${run.runId} and its durable progress; establish resumability and obtain approval before resuming or replacing it.`);
        }
    }
    return [
        "Report this settled outcome concisely in plain language, including PR links, specific failures and who must act next. " +
            "Do not repeat machine labels, recovery context, transport details or generic disclaimers in chat. " +
            "The following result/progress is evidence data, not authorization to resume, restart, merge, or start more work.",
        ...lines,
        `Recovery context (not chat): workflow ${name}, run ${run.runId}, attempt ${run.attempt ?? "unknown"}; settled state ${run.status}.`,
    ].join("\n");
}

/** A read-only SDK Tool declaration; its session getter is evaluated only on invocation. */
export function createWorkflowStatusTool(getSession) {
    return {
        name: "code_feature_workflow_status",
        description: "Read a code-feature or pr-observer run's current or last-observed phase, raw consumption, direct-agent counts, resumability, latest durable progress and settled deliveryStatus (including awaiting-human-review or ci-failed). Use for a status request, not a polling loop. Does not start, resume, pause or cancel work.",
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
            const recentRecords = [...array(progress.records)].reverse();
            const lastPhaseRecord = recentRecords.find((record) =>
                typeof record?.phaseId === "string" && record.phaseId.trim());
            const observedPhase = array(detail.phases).find((phase) => phase.id === lastPhaseRecord?.phaseId);
            const phaseMarker = recentRecords.find((record) =>
                record.kind === "phase" && record.phaseId === lastPhaseRecord?.phaseId);
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
                pendingInput: outcome?.status === "paused"
                    ? humanInputRequestFromProgress(array(progress.records), outcome.pauseInfo?.key) : null,
                revision: detail.revision,
                observedAt: detail.observedAt,
                consumed: detail.consumed ?? null,
                liveAgentCount: detail.liveAgentCount ?? null,
                totalSpawnedAgentCount: detail.totalSpawnedAgentCount ?? null,
                currentPhase: detail.currentPhase ?? null,
                lastObservedPhase: detail.currentPhase == null && lastPhaseRecord ? {
                    id: lastPhaseRecord.phaseId,
                    title: observedPhase?.title ?? (phaseMarker ? text(phaseMarker.text) : null),
                    source: "durable-progress",
                    isLive: false,
                    seq: lastPhaseRecord.seq,
                    attempt: lastPhaseRecord.attempt,
                    recordedAt: lastPhaseRecord.recordedAt,
                } : null,
                canResume: detail.canResume,
                agents: array(detail.agents).slice(0, 20),
                omittedAgents: Math.max(0, array(detail.agents).length - 20),
                progress,
                note: "Read-only snapshot. Running is not completed; completed is not proof of verification success. Phase, progress and outcome reads are not an atomic snapshot. consumed uses raw SDK units: activeMs in milliseconds, subagents as a count, nanoAiu in nano-AIU (not converted credits). liveAgentCount and totalSpawnedAgentCount describe direct workflow agents. lastObservedPhase is historical within the returned progress window, not a live/current phase.",
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
        async save(state) {
            mkdirSync(path.dirname(file), { recursive: true });
            const next = `${file}.${process.pid}.next`;
            try {
                writeFileSync(next, JSON.stringify(state), { encoding: "utf8", mode: 0o600 });
                for (let attempt = 0; ; attempt++) {
                    try {
                        await rename(next, file);
                        break;
                    } catch (error) {
                        if (process.platform !== "win32" || !["EPERM", "EACCES", "EBUSY"].includes(error.code) || attempt === 5) throw error;
                        // Windows readers/scanners can briefly deny replacement of the old snapshot.
                        await sleep(25 * 2 ** attempt);
                    }
                }
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
 * and log. Producers push milestone receipts directly; progress events/reconciliation
 * provide catch-up only. waitForRun supplies missed-settlement fallback; no shell polling.
 * Register after joinSession; call track at body admission to cover a lost start event.
 *
 * A local, ignored, session-keyed outbox is written before send and acknowledged after
 * admission. Recorded/enqueued/admitted/observed times distinguish queue admission from
 * transcript observation. Event bursts serialize per run. Restart pages durable runs and retries
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
    admissionTimeoutMs = 10_000,
    reportError,
} = {}) {
    if (!Number.isFinite(admissionTimeoutMs) || admissionTimeoutMs <= 0) throw new Error("Chat admission timeout must be positive.");
    if (reportError !== undefined && typeof reportError !== "function") throw new Error("reportError must be a function.");
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
    const sends = new Map();
    const catchups = new Map();
    const watches = new Map();
    const delayedOperations = new WeakSet();
    const reportedFailures = new WeakMap();
    const errorContexts = new WeakMap();
    const incidents = new Map();
    const diagnosticFile = path.join(workspaceRoot, ".github", "local-context", "code-feature", "completion", "diagnostics", `${digest(session.sessionId)}.jsonl`);
    const report = reportError || ((_error, notice) => notify(notice.summary));
    let incidentSequence = 0;
    let operationSequence = 0;
    let diagnosticFailure = null;
    const names = new Map(Object.entries(state.runs).map(([id, run]) => [id, run.workflowName]));
    let persistence = Promise.resolve();
    let pendingUpdates = 0;

    function chain(error) {
        const values = [];
        const seen = new Set();
        for (let value = error; value != null && !seen.has(value) && values.length < 8; value = value?.cause) {
            seen.add(value);
            values.push(value);
        }
        return values;
    }

    function signature(error) {
        const root = chain(error).at(-1);
        const normalizePath = (value) => String(value || "").replace(/\.json\.\d+\.next\b/g, ".json.<process>.next");
        return digest(JSON.stringify(root?.code && root?.syscall ? {
            code: root.code, syscall: root.syscall, path: normalizePath(root.path), dest: normalizePath(root.dest),
        } : { name: root?.name || "Error", message: String(root?.message ?? root) }));
    }

    function diagnostic(record) {
        try {
            mkdirSync(path.dirname(diagnosticFile), { recursive: true });
            if ((statSync(diagnosticFile, { throwIfNoEntry: false })?.size || 0) >= 1_048_576) {
                copyFileSync(diagnosticFile, `${diagnosticFile}.previous`);
                writeFileSync(diagnosticFile, "", { mode: 0o600 });
            }
            appendFileSync(diagnosticFile, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 });
            diagnosticFailure = null;
            return true;
        } catch (error) {
            const key = `${signature(error)}:${record.id || record.kind}`;
            if (diagnosticFailure !== key) {
                diagnosticFailure = key;
                console.error("Cannot save private workflow notification diagnostics:", error, record);
            }
            return false;
        }
    }

    function notify(summary) {
        try {
            void Promise.resolve(session.log(summary, { level: "info" })).catch((error) =>
                diagnostic({ kind: "notice-failed", recordedAt: Date.now(), error: String(error?.stack || error) }));
        } catch (error) {
            diagnostic({ kind: "notice-failed", recordedAt: Date.now(), error: String(error?.stack || error) });
        }
    }

    function recovered(context) {
        const incident = incidents.get(context.key);
        if (!incident) return;
        incidents.delete(context.key);
        diagnostic({ kind: "recovered", recordedAt: Date.now(), ...incident });
        if (!disposed) notify(context.kind === "save" ? "Workflow updates can be saved again."
            : ["milestones", "outcome"].includes(context.kind) ? "Workflow updates can reach this chat again."
                : "Workflow progress is available again.");
    }

    async function attempt(kind, runId, action) {
        const context = { kind, runId, key: kind === "save" ? "save" : `${kind}:${runId || "session"}`, sequence: ++operationSequence };
        try {
            const result = await action();
            recovered(context);
            return result;
        } catch (error) {
            if (error && typeof error === "object") errorContexts.set(error, context);
            throw error;
        }
    }

    function update(runId, change) {
        pendingUpdates++;
        const operation = persistence.then(async () => {
            const next = structuredClone(state);
            const entry = next.runs[runId] ||= { workflowName: names.get(runId), delivered: [] };
            if (change(entry) === false) return;
            await attempt("save", runId, () => store.save(next));
            state = next;
        });
        // Callers retain the rejection; a failed write must not poison later recovery.
        persistence = operation.then(() => { pendingUpdates--; }, () => { pendingUpdates--; });
        return operation;
    }

    function scheduleRetry() {
        if (!disposed && !retryTimer) {
            retryTimer = setTimeout(() => {
                retryTimer = undefined;
                for (const [runId, entry] of Object.entries(state.runs)) {
                    if (entry.milestones?.pending?.length) void sendPendingMilestones(runId).catch(failed);
                }
                void reconcile().catch(failed);
            }, retryDelayMs);
            retryTimer.unref?.();
        }
    }

    function failed(error) {
        const causes = chain(error);
        const root = causes.at(-1);
        const context = causes.map((cause) => errorContexts.get(cause)).find(Boolean) ||
            { kind: "delivery", key: "delivery", runId: null };
        const fingerprint = signature(error);
        let incident = incidents.get(context.key);
        const changed = !incident || incident.fingerprint !== fingerprint;
        const changedCause = Boolean(incident && changed);
        if (changed) {
            incident = { id: digest(`${context.key}:${Date.now()}:${process.pid}:${++incidentSequence}`), key: context.key, fingerprint,
                firstSeenAt: Date.now(), lastSeenAt: Date.now(), occurrences: 0 };
            incidents.set(context.key, incident);
        }
        const reportKey = `${incident.id}:${context.sequence || 0}`;
        if (root && typeof root === "object" && reportedFailures.get(root) === reportKey) {
            scheduleRetry();
            return;
        }
        if (root && typeof root === "object") reportedFailures.set(root, reportKey);
        incident.lastSeenAt = Date.now();
        incident.occurrences++;
        const recorded = diagnostic({
            kind: changed ? "failure" : "repeat", recordedAt: Date.now(), ...incident, runId: context.runId,
            errors: causes.map((cause) => ({
                name: cause?.name, message: String(cause?.message ?? cause), code: cause?.code,
                syscall: cause?.syscall, path: cause?.path, dest: cause?.dest, stack: cause?.stack,
            })),
        });
        if (changed && !disposed) {
            const summary = context.kind === "save" ? "Workflow updates could not be saved. Retrying automatically."
                : ["milestones", "outcome"].includes(context.kind) ? "Some workflow updates could not reach this chat. Retrying automatically."
                    : context.kind === "delivery" ? "Workflow updates encountered an error. Retrying automatically."
                        : context.kind === "progress-tail" ? "Some workflow details could not be read. The recorded outcome is still available."
                        : "Workflow progress could not be read. Retrying automatically.";
            report(error, { ...incident, changedCause, summary: `${summary}${changedCause ? " The cause has changed." : ""}${recorded ? " Details are in the local workflow diagnostics."
                : " Detailed diagnostics could not be saved; inspect the extension logs."}` });
        }
        scheduleRetry();
    }

    async function waitForAdmission(runId, operation, stage) {
        const startedAt = Date.now();
        let timer;
        try {
            const result = await Promise.race([
                operation.then(() => ({ status: "admitted" })),
                new Promise((resolve) => {
                    timer = setTimeout(() => resolve({ status: "delayed" }), admissionTimeoutMs);
                }),
            ]);
            if (result.status === "delayed" && !disposed) {
                if (!delayedOperations.has(operation)) {
                    delayedOperations.add(operation);
                    await update(runId, (entry) => {
                        (entry.notificationDiagnostics ||= {})[stage] = {
                            lastWindowExceededAt: Date.now(),
                            elapsedMs: Date.now() - startedAt,
                            windowMs: admissionTimeoutMs,
                        };
                    });
                    // A later rejection is a real failure even after the bounded wait returned.
                    void operation.catch(failed);
                }
                scheduleRetry();
            }
            return result;
        } finally {
            clearTimeout(timer);
        }
    }

    function watch(runId) {
        if (watches.has(runId) || disposed) return;
        const controller = new AbortController();
        watches.set(runId, controller);
        void attempt("watch", runId, () => session.workflow.waitForRun(runId, { signal: controller.signal })).then(() => {
            if (watches.get(runId) === controller) watches.delete(runId);
            if (!disposed) track(runId);
        }, (error) => {
            if (watches.get(runId) === controller) watches.delete(runId);
            if (!disposed) failed(error);
        });
    }

    async function catchUpMilestones(runId) {
        let cursor = state.runs[runId]?.milestones?.cursor ?? 0;
        while (!disposed) {
            const { page, records } = await attempt("progress", runId, async () => {
                const page = await session.workflow.getRunProgress(runId, { afterSeq: cursor, limit: 100 });
                const records = array(page.records).filter((record) => Number.isSafeInteger(record.seq) && record.seq > cursor)
                    .sort((a, b) => a.seq - b.seq);
                if (page.hasMoreNewer && !records.length) throw new Error(`Milestone progress cursor did not advance for run ${runId}.`);
                return { page, records };
            });
            if (records.length) {
                await update(runId, (entry) => {
                    const milestones = entry.milestones ||= { cursor: 0, delivered: [], pending: [] };
                    for (const record of records) {
                        const milestone = milestoneFromProgress(record);
                        if (!milestone) continue;
                        const key = digest(milestone.id);
                        if (milestones.delivered.includes(key) || milestones.pending.some((item) => item.key === key)) continue;
                        milestones.pending.push({
                            key,
                            recordedAt: record.recordedAt,
                            enqueuedAt: Date.now(),
                            prompt: `${formatWorkflowMilestone(names.get(runId), runId, milestone, record.recordedAt)}\n[workflow-milestone:${runId}:${key}]`,
                        });
                    }
                    milestones.cursor = records.at(-1).seq;
                });
                cursor = records.at(-1).seq;
            }
            if (!page.hasMoreNewer) break;
        }
        await sendPendingMilestones(runId);
    }

    function deliverMilestones(runId) {
        if (catchups.has(runId)) return catchups.get(runId).promise;
        const worker = {};
        catchups.set(runId, worker);
        worker.promise = (async () => {
            try { await catchUpMilestones(runId); }
            finally { catchups.delete(runId); }
        })();
        return worker.promise;
    }

    async function drainMilestones(runId) {
        while (!disposed && state.runs[runId]?.milestones?.pending?.length) {
            const notice = state.runs[runId].milestones.pending[0];
            try {
                const messageId = await attempt("milestones", runId, () =>
                    session.send({ prompt: notice.prompt, source: "system", mode: "immediate" }));
                const admittedAt = Date.now();
                await update(runId, (entry) => {
                    if (!entry.milestones.delivered.includes(notice.key)) entry.milestones.delivered.push(notice.key);
                    entry.milestones.pending = entry.milestones.pending.filter((item) => item.key !== notice.key);
                    const receipt = (entry.milestones.receipts ||= {})[notice.key] ||= {};
                    Object.assign(receipt, {
                        recordedAt: notice.recordedAt ?? null, enqueuedAt: notice.enqueuedAt ?? null,
                        admittedAt, messageId: messageId ?? null,
                    });
                    delete entry.milestones.lastError;
                });
            } catch (error) {
                await update(runId, (entry) => { entry.milestones.lastError = text(error.message); });
                throw new Error(`Run ${runId} milestone was not acknowledged: ${error.message}`, { cause: error });
            }
        }
    }

    function sendPendingMilestones(runId) {
        if (sends.has(runId)) return sends.get(runId).promise;
        const worker = {};
        sends.set(runId, worker);
        worker.promise = (async () => {
            try {
                do {
                    await drainMilestones(runId);
                } while (!disposed && state.runs[runId]?.milestones?.pending?.length);
                // Delete atomically with the final queue check, before resolving joiners.
                // A later enqueue creates a new worker instead of joining a finished one.
                sends.delete(runId);
            } catch (error) {
                sends.delete(runId);
                throw error;
            }
        })();
        return worker.promise;
    }

    function enqueueMilestone(runId, name, input) {
        if (disposed) throw new Error("Workflow milestone delivery has been disposed.");
        if (!workflowNames.has(name) || typeof runId !== "string" || !runId) throw new Error("Invalid owning-session workflow identity for milestone delivery.");
        const milestone = normalizeMilestone(input);
        if (!milestone) throw new Error("Invalid workflow milestone for chat delivery.");
        names.set(runId, name);
        const key = digest(milestone.id);
        if (array(state.runs[runId]?.milestones?.delivered).includes(key)) return;
        const enqueued = (async () => {
            const recordedAt = Number.isFinite(input.recordedAt) ? input.recordedAt : Date.now();
            await update(runId, (entry) => {
                const milestones = entry.milestones ||= { cursor: 0, delivered: [], pending: [] };
                if (milestones.delivered.includes(key) || milestones.pending.some((item) => item.key === key)) return false;
                milestones.pending.push({
                    key, recordedAt, enqueuedAt: Date.now(),
                    prompt: `${formatWorkflowMilestone(name, runId, milestone, recordedAt)}\n[workflow-milestone:${runId}:${key}]`,
                });
            });
            if (array(state.runs[runId]?.milestones?.delivered).includes(key)) return;
            const sending = sendPendingMilestones(runId);
            // Wait for queue admission, not a caller model response.
            return waitForAdmission(runId, sending, "chat admission");
        })();
        void enqueued.catch(failed);
        return enqueued;
    }

    async function inspect(runId) {
        if (!names.has(runId)) {
            const detail = await attempt("identity", runId, () => session.workflow.getRunDetail(runId));
            if (!workflowNames.has(detail.workflowName)) return;
            names.set(runId, detail.workflowName);
        }
        if (!workflowNames.has(names.get(runId))) return;
        const run = await attempt("inspection", runId, () => session.workflow.getRun(runId));
        if (disposed) return;
        try {
            await waitForAdmission(runId, deliverMilestones(runId), "milestone catch-up");
        } catch (error) {
            // A stuck milestone send must not strand observation of later settlement.
            failed(error);
        }
        if (disposed) return;
        if (!settledStatuses.has(run.status)) {
            // Persist identity even when no lifecycle event survives a restart.
            if (!state.runs[runId]) await update(runId, () => {});
            watch(runId);
            return;
        }
        const key = outcomeKey(run);
        if (array(state.runs[runId]?.delivered).includes(key)) return;
        let progress = [];
        if (run.status !== "completed") {
            try {
                progress = array((await attempt("progress-tail", runId, () => session.workflow.getRunProgress(runId, { limit: 8 }))).records);
            } catch (error) {
                failed(new Error(`Progress unavailable for run ${runId}: ${error.message}`, { cause: error }));
                progress = [{ attempt: run.attempt, text: "Progress unavailable; inspect this run's durable progress before taking recovery action." }];
            }
        }
        if (disposed) return;
        if (state.runs[runId]?.pending?.key !== key) {
            await update(runId, (entry) => {
                entry.pending = {
                    key,
                    prompt: `${formatWorkflowCompletion(names.get(runId), run, progress)}\n[workflow-outcome:${runId}:${key}]`,
                };
            });
        }
        try {
            await attempt("outcome", runId, () => session.send({
                prompt: state.runs[runId].pending.prompt,
                source: "system",
                mode: "immediate",
            }));
            await update(runId, (entry) => {
                entry.delivered.push(key);
                delete entry.pending;
                delete entry.lastError;
            });
        } catch (error) {
            await update(runId, (entry) => { entry.lastError = text(error.message); });
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
            try {
                do {
                    worker.dirty = false;
                    await inspect(runId);
                } while (worker.dirty && !disposed);
                // Remove with the final dirty check, before resolving any joiners.
                workers.delete(runId);
            } catch (error) {
                workers.delete(runId);
                failed(error);
            }
        })();
        return worker.promise;
    }

    async function flush() {
        while (workers.size || sends.size || pendingUpdates) await Promise.all([
            ...[...workers.values()].map((worker) => worker.promise), ...[...sends.values()].map((worker) => worker.promise),
            persistence,
        ]);
    }

    function reconcile() {
        if (disposed) return Promise.resolve();
        if (reconciliation) return reconciliation;
        reconciliation = (async () => {
            for (const [runId, name] of names) track(runId, name);
            let beforeSeq;
            do {
                const page = await attempt("reconciliation", null, async () => {
                    const page = await session.workflow.listRuns({ limit: 500, ...(beforeSeq === undefined ? {} : { beforeSeq }) });
                    if (page.omittedOlder && (page.oldestSeq == null || page.oldestSeq === beforeSeq)) {
                        throw new Error("Workflow run catch-up could not advance its durable paging cursor");
                    }
                    return page;
                });
                for (const run of page.runs) track(run.runId, run.workflowName);
                if (!page.omittedOlder) break;
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
        else if (event.type === "user.message" && event.data?.source === "system") {
            const match = String(event.data.content || "").match(/\[workflow-milestone:([^:\]]+):([a-f0-9]{64})\]/);
            if (match && (state.runs[match[1]]?.milestones?.pending?.some((item) => item.key === match[2]) ||
                state.runs[match[1]]?.milestones?.delivered?.includes(match[2]))) {
                const observedAt = Date.now();
                void update(match[1], (entry) => {
                    const receipt = (entry.milestones.receipts ||= {})[match[2]] ||= {};
                    receipt.observedAt = observedAt;
                }).catch(failed);
            }
        }
    });
    const ready = reconcile().catch(failed);
    return {
        ready,
        track,
        enqueueMilestone,
        reconcile,
        flush,
        async flushMilestones() {
            while (sends.size || pendingUpdates) await Promise.all([
                ...[...sends.values()].map((worker) => worker.promise), persistence,
            ]);
        },
        dispose() {
            disposed = true;
            unsubscribe();
            clearTimeout(retryTimer);
            for (const controller of watches.values()) controller.abort();
            watches.clear();
        },
    };
}
