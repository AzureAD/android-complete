// Copyright (c) Microsoft Corporation. All rights reserved.

import type { CopilotSession, WorkflowRunResult, WorkflowRunDetail, WorkflowLimitOverrides } from "@github/copilot-sdk";
import { randomUUID } from "node:crypto";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, realpathSync } from "node:fs";
import path from "node:path";
import { FactoryError, type Actor } from "../core/contracts.ts";
import { jsonValue, type Execution, type ExecutionLimits, type Json, type WorkflowAdmission, type WorkflowKind } from "../core/lifecycle-contracts.ts";
import type { Lifecycle } from "../core/lifecycle.ts";
import { now } from "../core/lifecycle-store.ts";
import { hash } from "../core/lifecycle-store.ts";
import { fileEvidence, loadedWorkflowFingerprint, runtimeFingerprint } from "../core/evidence.ts";
import { systemRunner } from "../providers/cli.ts";
import { createClient, ownedRuntimeEnvironment, sessionConfiguration } from "./host.ts";
import { requireRuntime } from "../core/runtime-health.ts";
import { awaitWorkflowRegistration } from "./registration.ts";
import { resolveExtensionInterpreter } from "./interpreter.ts";
import { FACTORY_READER, initializeReaderPipeline } from "./reader.ts";
import type { SharedAuthority } from "../core/shared.ts";

export interface NativeSession {
    sessionId: string;
    workflow: Pick<CopilotSession["workflow"], "run" | "resume" | "pause" | "cancel" | "getRun" | "getRunDetail">;
    rpc: {
        extensions: Pick<CopilotSession["rpc"]["extensions"], "list" | "reload">;
        tools: Pick<CopilotSession["rpc"]["tools"], "execute" | "initializeAndValidate" | "getCurrentMetadata">;
    };
}

export async function sourcePins(factory: Lifecycle): Promise<Record<string, string>> {
    const workspace = factory.store.registry.project.workspace;
    const roots = new Set([workspace]);
    const config = factory.store.configuration();
    for (const repo of Object.values(config?.value.repositories || {})) {
        const location = path.resolve(workspace, repo.localPath);
        if (existsSync(location)) roots.add(realpathSync(location));
    }
    const pins: Record<string, string> = {};
    for (const root of roots) {
        const head = await systemRunner.run("git", ["rev-parse", "HEAD"], { cwd: root });
        const digest = createHash("sha256").update(await systemRunner.run("git", ["--no-pager", "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD"], { cwd: root }));
        const files = await systemRunner.run("git", ["ls-files", "--others", "--exclude-standard", "-z"], { cwd: root });
        for (const file of files.split("\0").filter(Boolean).sort()) digest.update(file).update(fileEvidence(path.join(root, file)));
        pins[root] = `${head}:${digest.digest("hex")}`;
    }
    return pins;
}

export class NativeRuntime {
    private readonly factory: Lifecycle;
    private client?: ReturnType<typeof createClient>;
    private readonly sessions = new Map<string, NativeSession>();
    private readonly launching = new Set<string>();
    private readonly ownerToken = randomUUID();
    private readonly joined?: NativeSession;
    private readonly shared?: SharedAuthority;
    private readonly leaseSessions = new Set<string>();
    private readonly activeControls = new Set<string>();
    private readonly timer: NodeJS.Timeout;
    private started = false;
    private polling = false;
    private stopping = false;
    private readonly pending = new Set<Promise<void>>();

    constructor(factory: Lifecycle, joined?: NativeSession, shared?: SharedAuthority) {
        this.factory = factory;
        this.joined = joined;
        this.shared = shared;
        if (joined) {
            factory.store.heartbeat(joined.sessionId, this.ownerToken);
            this.leaseSessions.add(joined.sessionId);
        }
        this.timer = setInterval(() => { void this.poll().catch((error: unknown) => console.error("[Software Factory] Runtime control failed:", error)); }, 1000);
        this.timer.unref();
    }

    async start(featureId: string, kind: WorkflowKind, actor: Actor, limits: ExecutionLimits = {},
        admission?: WorkflowAdmission): Promise<Execution> {
        await this.shared?.pull(featureId);
        const needsClaim = Boolean(this.shared && kind !== "check" && (kind !== "intake" || this.shared.cached(featureId)));
        const execution = this.factory.begin(featureId, kind, actor, {}, limits, admission,
            this.joined ? { sessionId: this.joined.sessionId, kind: "chat", established: true } : undefined,
            needsClaim ? "Claiming shared execution" : "Starting native workflow");
        try {
            if (needsClaim) {
                await this.shared!.claimRun(featureId, execution);
                this.factory.store.updateExecution(featureId, execution.id, { phase: "Starting native workflow" });
            }
            this.launch(featureId, this.factory.store.execution(featureId, execution.id));
        } catch (error) {
            this.fail(featureId, execution.id, error);
            throw error;
        }
        return this.factory.store.execution(featureId, execution.id);
    }

    launch(featureId: string, execution: Execution): void {
        const current = this.factory.store.execution(featureId, execution.id);
        if (current.status !== "starting" || current.runtimeId) return;
        if (execution.sessionKind === "chat" && execution.sessionId !== this.joined?.sessionId) {
            this.factory.store.updateExecution(featureId, execution.id, { phase: this.factory.store.ownerOnline(execution.sessionId)
                ? "Queued for the owning Copilot chat" : "Waiting for the owning Copilot chat to reconnect" });
            return;
        }
        if (execution.sessionKind !== "chat" && this.factory.store.ownerOnline(execution.sessionId) &&
            !this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) {
            this.factory.store.updateExecution(featureId, execution.id, { phase: "Queued for the owning Factory runtime" });
            return;
        }
        if (current.admissionClaim && !current.runtimeId) {
            this.fail(featureId, execution.id, new FactoryError("native_admission_uncertain", "A prior controller claimed this admission without recording a native run ID. Inspect/reconcile it before authorizing a successor.", 409));
            return;
        }
        if (this.launching.has(execution.id)) return;
        this.launching.add(execution.id);
        const pending = (async () => {
            await this.shared?.assertRun(featureId, execution.id);
            const active = this.factory.store.execution(featureId, execution.id);
            if (active.status !== "starting" || active.runtimeId) return;
            if (active.sessionKind === "chat" && process.env.SESSION_ID === active.sessionId &&
                this.joined?.sessionId === active.sessionId && loadedWorkflowFingerprint !== active.runtimeFingerprint) {
                this.factory.store.updateExecution(featureId, execution.id, { phase: "Reloading Software Factory integration" });
                this.stopping = true;
                clearInterval(this.timer);
                for (const id of this.leaseSessions) this.factory.store.releaseOwner(id, this.ownerToken);
                this.leaseSessions.clear();
                await this.joined.rpc.extensions.reload();
                return;
            }
            this.factory.store.updateExecution(featureId, execution.id, { phase: this.joined ? "Using the current Copilot chat" : "Checking local Copilot runtime" });
            if (execution.sessionKind !== "chat") {
                const runtime = requireRuntime();
                const node = await resolveExtensionInterpreter(ownedRuntimeEnvironment(process.env));
                this.factory.store.updateExecution(featureId, execution.id, { launchContext: {
                    hostExecutable: process.execPath, extensionNode: node.executable, nodeVersion: node.version, nativeRuntime: runtime.runtimeExecutable,
                } });
            }
            if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped during Node verification. No workflow was admitted.", 409);
            this.factory.assertFresh(featureId, execution.id);
            const pins = await sourcePins(this.factory);
            if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped before workflow admission. No agents were launched; explicitly start again when ready.", 409);
            this.factory.assertFresh(featureId, execution.id);
            this.factory.store.updateExecution(featureId, execution.id, { sourcePins: pins, phase: "Starting Copilot session" });
            await this.run(featureId, this.factory.store.execution(featureId, execution.id));
        })().catch((error: unknown) => this.fail(featureId, execution.id, error));
        this.pending.add(pending);
        void pending.finally(() => { this.pending.delete(pending); this.launching.delete(execution.id); });
    }

    private async session(execution: Execution): Promise<NativeSession> {
        if (this.stopping) throw new FactoryError("runtime_stopping", "This host is shutting down; it cannot start or recover a session.", 409);
        const existing = this.sessions.get(execution.id);
        if (existing) return existing;
        if (execution.sessionKind === "chat") {
            if (!this.joined || this.joined.sessionId !== execution.sessionId) {
                throw new FactoryError("chat_owner_unavailable", "Reconnect the owning Copilot chat. Another SDK client cannot take over its session.", 409);
            }
            this.sessions.set(execution.id, this.joined);
            return this.joined;
        }
        const resident = [...this.sessions.values()].find((session) => session.sessionId === execution.sessionId);
        if (resident) { this.sessions.set(execution.id, resident); return resident; }
        this.factory.store.heartbeat(execution.sessionId, this.ownerToken);
        this.leaseSessions.add(execution.sessionId);
        if (!this.started) {
            requireRuntime();
            this.client ??= createClient(this.factory.store.registry.project.workspace, path.dirname(this.factory.store.registry.databasePath));
            await this.client.start(); this.started = true;
            if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped during Copilot startup. No workflow was launched.", 409);
        }
        const evidence = path.join(path.dirname(this.factory.store.registry.databasePath), "approval-evidence");
        mkdirSync(evidence, { recursive: true, mode: 0o700 });
        const config = sessionConfiguration(this.factory.store.registry.project.workspace, `Software Factory ${execution.kind} ${execution.id}`, [
            ...Object.keys(execution.sourcePins).filter((root) => root !== this.factory.store.registry.project.workspace), evidence,
        ]);
        const session = execution.runtimeId || execution.reuseSession
            ? await this.client!.resumeSession(execution.sessionId, config)
            : await this.client!.createSession({ ...config, sessionId: execution.sessionId });
        this.sessions.set(execution.id, session);
        for (const feature of this.factory.store.registry.list()) {
            if (this.factory.store.state(feature.id).sessionBinding?.sessionId === execution.sessionId) {
                this.factory.store.change(feature.id, (state) => { if (state.sessionBinding) state.sessionBinding.established = true; });
            }
        }
        return session;
    }

    private async registration(featureId: string, execution: Execution, session: NativeSession): Promise<void> {
        this.assertOwner(execution);
        this.factory.assertFresh(featureId, execution.id);
        this.factory.store.updateExecution(featureId, execution.id, { phase: "Verifying workflow registration" });
        const registration = await awaitWorkflowRegistration(session, `software-factory-${execution.kind}`, execution.runtimeFingerprint);
        if (registration.readerAgent !== FACTORY_READER) throw new FactoryError("worker_policy_missing", "The owning session did not register the scoped read-only Factory worker. No workflow was started.", 503);
        this.factory.store.updateExecution(featureId, execution.id, { phase: "Checking scoped worker read tools" });
        await initializeReaderPipeline(session);
        if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped during registration. No new workflow was admitted.", 409);
        this.assertOwner(execution);
        this.factory.assertFresh(featureId, execution.id);
        this.factory.store.updateExecution(featureId, execution.id, { registration, phase: "Workflow registration verified" });
    }

    private assertOwner(execution: Execution): void {
        if (!this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) {
            throw new FactoryError("chat_owner_lease_lost", "This controller no longer owns the chat session. No new native action was admitted.", 409);
        }
    }

    private settle(featureId: string, executionId: string, result: WorkflowRunResult): void {
        const current = this.factory.store.execution(featureId, executionId);
        if (["superseded", "cancelled", "completed"].includes(current.status) && current.status !== result.status) return;
        const status = result.status === "halted" ? "error" : result.status === "pending" ? "starting" : result.status;
        this.factory.store.updateExecution(featureId, executionId, {
            runtimeId: result.runId, status,
            outcome: result.status === "completed" && result.result !== undefined ? jsonValue(result.result) : null,
            error: result.failure ? JSON.stringify(result.failure) : result.error || null,
        });
    }

    private fail(featureId: string, executionId: string, error: unknown): void {
        const existing = this.factory.store.execution(featureId, executionId);
        if (["superseded", "cancelled", "completed"].includes(existing.status) || (existing.status === "error" && !existing.runtimeId)) return;
        if ((existing.sessionKind === "chat" || this.factory.store.ownerOnline(existing.sessionId)) &&
            !this.factory.store.ownsSession(existing.sessionId, this.ownerToken)) {
            this.factory.store.change(featureId, (state) => this.factory.store.event(state, "stale_chat_controller_rejected", executionId));
            return;
        }
        this.factory.store.updateExecution(featureId, executionId, {
            status: "error", phase: existing.runtimeId ? "Workflow stopped" : "Could not start workflow",
            errorCode: error instanceof FactoryError ? error.code : "workflow_failed",
            error: error instanceof Error ? error.message : "Native workflow execution failed.",
        });
    }

    private async observeUsage(featureId: string, executionId: string, session: NativeSession, runId: string): Promise<WorkflowRunDetail | null> {
        try {
            const detail = await session.workflow.getRunDetail(runId);
            if (detail.runId !== runId) throw new FactoryError("wrong_workflow_owner", "Native observation returned a different run.", 409);
            this.factory.store.updateExecution(featureId, executionId, {
                usage: {
                    observedAt: detail.observedAt, ...detail.consumed,
                    declaredLimits: { ...detail.declaredLimits }, approvedLimits: detail.approved ? { ...detail.approved } : null,
                    agents: detail.agents.map((agent) => ({
                        label: agent.label, status: agent.status, requestedModel: agent.requestedModel ?? null,
                        resolvedModel: agent.resolvedModel ?? null, activeMs: agent.activeMs,
                    })),
                },
                observationError: null,
            });
            return detail;
        } catch (error) {
            this.factory.store.updateExecution(featureId, executionId, {
                observationError: `Native usage observation failed: ${error instanceof Error ? error.message : "unknown runtime response"}. Refresh the owning run; previous measurements may be stale.`,
            });
            return null;
        }
    }

    private async run(featureId: string, execution: Execution): Promise<void> {
        const session = await this.session(execution);
        if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped before native workflow admission. No workflow was launched.", 409);
        this.factory.assertFresh(featureId, execution.id);
        const names: Record<WorkflowKind, string> = {
            intake: "software-factory-intake", design: "software-factory-design", plan: "software-factory-plan", lifecycle: "software-factory-lifecycle",
            monitor: "software-factory-monitor", check: "software-factory-check",
        };
        await this.registration(featureId, execution, session);
        await this.shared?.assertRun(featureId, execution.id);
        this.factory.store.change(featureId, (state) => {
            const current = state.executions.find((item) => item.id === execution.id)!;
            if (current.admissionClaim || current.runtimeId) throw new FactoryError("native_admission_exists", "This execution was already submitted or its admission is uncertain. Inspect the owning run; do not submit it twice.", 409);
            current.admissionClaim = this.ownerToken;
        });
        const result = await session.workflow.run(names[execution.kind], {
            args: { featureId, executionId: execution.id }, notifyOnComplete: execution.sessionKind === "chat" && execution.kind !== "check", logPhaseNames: true,
            limits: { maxAiCredits: null, ...execution.limits },
        });
        this.settle(featureId, execution.id, result);
        await this.observeUsage(featureId, execution.id, session, result.runId);
    }

    async control(featureId: string, executionId: string, action: "pause" | "resume" | "cancel" | "refresh", actor: Actor, limits: WorkflowLimitOverrides = {}): Promise<Json> {
        const execution = this.factory.store.execution(featureId, executionId);
        if (action === "cancel" && execution.status === "starting" && !execution.runtimeId) {
            this.factory.store.updateExecution(featureId, executionId, { status: "cancelled", phase: "Cancelled before native admission" });
            return { executionId, status: "cancelled", agentsStarted: 0 };
        }
        if (action === "refresh") {
            if (!execution.runtimeId) return jsonValue(execution);
            if ((execution.sessionKind === "chat" && this.joined?.sessionId !== execution.sessionId) ||
                (this.factory.store.ownerOnline(execution.sessionId) && !this.factory.store.ownsSession(execution.sessionId, this.ownerToken))) {
                return { execution: jsonValue(execution), observation: "Saved owner measurements; only the resident chat may refresh native state.",
                    ownerOnline: this.factory.store.ownerOnline(execution.sessionId) };
            }
            const session = await this.session(execution);
            this.assertOwner(execution);
            this.settle(featureId, executionId, await session.workflow.getRun(execution.runtimeId));
            const detail = await this.observeUsage(featureId, executionId, session, execution.runtimeId);
            if (!detail) throw new FactoryError("native_observation_failed", this.factory.store.execution(featureId, executionId).observationError!, 502);
            return jsonValue(detail);
        }
        if (["completed", "cancelled", "superseded"].includes(execution.status)) {
            throw new FactoryError("terminal_execution", "This execution is terminal. Inspect it or start an explicit successor; do not mutate its runtime outcome.", 409);
        }
        if (action === "pause" && execution.status !== "running" && execution.status !== "starting") {
            throw new FactoryError("run_not_running", "Only an active run can be paused.", 409);
        }
        if (action === "resume") {
            await this.shared?.assertRun(featureId, executionId);
            this.factory.assertFresh(featureId, executionId);
            if (!execution.runtimeId || !["paused", "error"].includes(execution.status)) throw new FactoryError("run_not_resumable", "Only a paused or limit/interruption-stopped native run can be resumed.", 409);
            if ((execution.kind === "intake" || execution.kind === "design" || !this.factory.store.latest(featureId, "plan")) &&
                JSON.stringify(await sourcePins(this.factory)) !== JSON.stringify(execution.sourcePins)) {
                throw new FactoryError("source_drift", "Source revisions changed while design/planning was paused. Start an explicitly reviewed successor.", 409);
            }
        }
        const id = randomUUID();
        this.factory.store.change(featureId, (state) => {
            state.controls.push({
                id, executionId, action, limits: Object.fromEntries(Object.entries(limits)
                    .filter((entry): entry is [string, number | null] => typeof entry[1] === "number" || entry[1] === null)),
                status: "pending", error: null, actor,
            });
        });
        await this.poll();
        return { requestId: id, status: "queued", executionId, action };
    }

    private async poll(): Promise<void> {
        if (this.polling || this.stopping) return;
        this.polling = true;
        try {
            for (const id of this.leaseSessions) this.factory.store.heartbeat(id, this.ownerToken);
            for (const feature of this.factory.store.registry.list()) {
                const state = this.factory.store.state(feature.id);
                for (const execution of state.executions) {
                    const residentOwner = this.factory.store.ownsSession(execution.sessionId, this.ownerToken);
                    const claimingShared = execution.phase === "Claiming shared execution" ||
                        Boolean(this.shared?.cached(feature.id) && execution.kind !== "check" &&
                            this.shared.cached(feature.id)?.snapshot.lock?.id !== execution.id);
                    if (residentOwner && !claimingShared &&
                        execution.status === "starting" && !execution.runtimeId && !this.launching.has(execution.id)) {
                        this.launch(feature.id, execution);
                    }
                    if (residentOwner &&
                        execution.status === "running" && execution.runtimeId) {
                        const session = await this.session(execution);
                        await this.observeUsage(feature.id, execution.id, session, execution.runtimeId);
                    }
                }
                for (const control of state.controls.filter((item) => item.status === "pending")) {
                    if (this.activeControls.has(control.id)) continue;
                    const execution = this.factory.store.execution(feature.id, control.executionId);
                    if (!execution.runtimeId) continue;
                    if (execution.sessionKind === "chat" && execution.sessionId !== this.joined?.sessionId) continue;
                    if (this.factory.store.ownerOnline(execution.sessionId) && !this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) continue;
                    // A resident owner handles running work. A cold owner may recover a settled run.
                    if (execution.sessionKind !== "chat" && !this.sessions.has(execution.id) && ["starting", "running"].includes(execution.status)) continue;
                    const claimed = this.factory.store.change(feature.id, (current) => {
                        const request = current.controls.find((item) => item.id === control.id)!;
                        if (request.status !== "pending") return false;
                        request.status = "running";
                        return true;
                    });
                    if (!claimed) continue;
                    this.activeControls.add(control.id);
                    const task = (async () => {
                        try {
                            const session = await this.session(execution);
                            if (!execution.runtimeId) throw new FactoryError("run_not_started", "The native run has not been admitted yet.", 409);
                            this.assertOwner(execution);
                            if (control.action === "resume") {
                                this.factory.assertFresh(feature.id, execution.id);
                                await this.registration(feature.id, execution, session);
                                await this.shared?.assertRun(feature.id, execution.id);
                                this.factory.store.updateExecution(feature.id, execution.id, { status: "running" });
                                this.markControl(feature.id, control.id);
                                const result = await session.workflow.resume(execution.runtimeId, { limits: control.limits,
                                    notifyOnComplete: execution.sessionKind === "chat" && execution.kind !== "check", logPhaseNames: true });
                                this.settle(feature.id, execution.id, result);
                                await this.observeUsage(feature.id, execution.id, session, result.runId);
                            } else {
                                const result = control.action === "pause"
                                    ? await session.workflow.pause(execution.runtimeId) : await session.workflow.cancel(execution.runtimeId);
                                this.settle(feature.id, execution.id, result);
                                this.markControl(feature.id, control.id);
                                await this.observeUsage(feature.id, execution.id, session, result.runId);
                            }
                        } catch (error) {
                            this.markControl(feature.id, control.id, error instanceof Error ? error.message : "Native control failed.");
                            const current = this.factory.store.execution(feature.id, execution.id);
                            if (!["superseded", "cancelled", "completed"].includes(current.status)) {
                                this.factory.store.updateExecution(feature.id, execution.id, {
                                    status: "error", error: error instanceof Error ? error.message : "Control failed; refresh the actual native outcome.",
                                });
                            }
                        } finally { this.activeControls.delete(control.id); }
                    })();
                    this.pending.add(task);
                    void task.finally(() => this.pending.delete(task));
                }
                if (state.schedule && new Date(state.schedule.nextAt).getTime() <= Date.now()) {
                    const currentScope = hash({
                        requirementsRevision: feature.current.id, planId: this.factory.store.latest(feature.id, "plan")?.id || null,
                        configurationId: this.factory.store.configuration()?.id || null,
                        runtimeFingerprint: runtimeFingerprint(),
                    });
                    if (state.schedule.scopeHash !== currentScope) {
                        this.factory.store.change(feature.id, (current) => {
                            current.schedule = null; this.factory.store.event(current, "schedule_scope_changed", currentScope);
                        });
                        continue;
                    }
                    const active = state.executions.some((item) => item.kind === "monitor" && ["starting", "running", "paused"].includes(item.status));
                    this.factory.store.change(feature.id, (current) => {
                        if (current.schedule) current.schedule.nextAt = new Date(Date.now() + current.schedule.minutes * 60_000).toISOString();
                    });
                    if (!active) await this.start(feature.id, "monitor", state.schedule.actor);
                }
            }
        } finally { this.polling = false; }
    }

    private markControl(featureId: string, id: string, error: string | null = null) {
        this.factory.store.change(featureId, (state) => {
            const control = state.controls.find((item) => item.id === id)!;
            control.status = error ? "error" : "completed";
            control.error = error;
            this.factory.store.event(state, error ? "runtime_control_failed" : "runtime_control_admitted", id);
        });
    }

    async close(): Promise<void> {
        this.stopping = true;
        clearInterval(this.timer);
        for (const feature of this.factory.store.registry.list()) {
            for (const execution of this.factory.store.state(feature.id).executions) {
                const session = this.sessions.get(execution.id);
                if (session && execution.runtimeId && execution.status === "running" &&
                    this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) {
                    try { this.settle(feature.id, execution.id, await session.workflow.pause(execution.runtimeId)); }
                    catch (error) { this.fail(feature.id, execution.id, error); }
                }
            }
        }
        const errors = await this.client?.stop() || [];
        await Promise.allSettled([...this.pending]);
        if (this.client) errors.push(...await this.client.stop());
        for (const id of this.leaseSessions) this.factory.store.releaseOwner(id, this.ownerToken);
        if (errors.length) throw new AggregateError(errors, "Workflow runtime cleanup failed; inspect interrupted runs before recovery.");
    }
}
