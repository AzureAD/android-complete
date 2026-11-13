// Copyright (c) Microsoft Corporation. All rights reserved.

import type { CopilotSession, WorkflowRunResult, WorkflowRunDetail, WorkflowLimitOverrides } from "@github/copilot-sdk";
import { randomUUID } from "node:crypto";
import { mkdirSync, realpathSync } from "node:fs";
import path from "node:path";
import { FactoryError, type Actor } from "../core/contracts.ts";
import { deliverySettings, jsonValue, type DeliverySettings, type Execution, type ExecutionLimits, type Json, type WorkflowAdmission, type WorkflowKind } from "../core/lifecycle-contracts.ts";
import type { Lifecycle } from "../core/lifecycle.ts";
import { now } from "../core/lifecycle-store.ts";
import { hash } from "../core/lifecycle-store.ts";
import { loadedWorkflowFingerprint, runtimeFingerprint } from "../core/evidence.ts";
import type { createClient } from "./host.ts";
import { ensureSubrepoWorktree, requireRuntime } from "../core/runtime-health.ts";
import { systemRunner } from "../providers/cli.ts";
import { awaitWorkflowRegistration } from "./registration.ts";
import { resolveExtensionInterpreter } from "./interpreter.ts";
import { FACTORY_READER, initializeReaderPipeline } from "./reader.ts";
import type { SharedAuthority } from "../core/shared.ts";
import { sourcePins } from "./sources.ts";
export { sourcePins, repositorySourceRoot } from "./sources.ts";

export interface NativeSession {
    sessionId: string;
    workflow: Pick<CopilotSession["workflow"], "run" | "resume" | "pause" | "cancel" | "getRun" | "getRunDetail">;
    rpc: {
        extensions: Pick<CopilotSession["rpc"]["extensions"], "list" | "reload">;
        tools: Pick<CopilotSession["rpc"]["tools"], "execute" | "initializeAndValidate" | "getCurrentMetadata">;
    };
}

export class NativeRuntime {
    private readonly factory: Lifecycle;
    private client?: ReturnType<typeof createClient>;
    private readonly ahpHosts = new Map<string, Awaited<ReturnType<ReturnType<typeof createClient>["startAhpHost"]>>>();
    private readonly sessions = new Map<string, NativeSession>();
    private readonly launching = new Set<string>();
    private readonly ownerToken = randomUUID();
    private readonly joined?: NativeSession;
    private readonly shared?: SharedAuthority;
    private readonly leaseSessions = new Set<string>();
    private readonly heartbeats = new Map<string, number>();
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
            this.heartbeats.set(joined.sessionId, Date.now());
        }
        this.timer = setInterval(() => { void this.poll().catch((error: unknown) => console.error("[Software Factory] Runtime control failed:", error)); }, 1000);
        this.timer.unref();
    }

    async start(featureId: string, kind: WorkflowKind, actor: Actor, limits: ExecutionLimits = {},
        admission?: WorkflowAdmission, delivery?: Partial<DeliverySettings>): Promise<Execution> {
        await this.shared?.pull(featureId);
        const needsClaim = Boolean(this.shared && kind !== "check" && (kind !== "intake" || this.shared.cached(featureId)));
        const execution = this.factory.begin(featureId, kind, actor, {}, limits, admission,
            this.joined ? { sessionId: this.joined.sessionId, kind: "chat", established: true } : undefined,
            needsClaim ? "Claiming shared execution" : "Starting native workflow", delivery);
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
            if (execution.kind !== "check") await this.shared?.assertRun(featureId, execution.id);
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
                const { ownedRuntimeEnvironment } = await import("./host.ts");
                const node = await resolveExtensionInterpreter(ownedRuntimeEnvironment(process.env));
                this.factory.store.updateExecution(featureId, execution.id, { launchContext: {
                    hostExecutable: process.execPath, extensionNode: node.executable, nodeVersion: node.version, nativeRuntime: runtime.runtimeExecutable,
                } });
            }
            if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped during Node verification. No workflow was admitted.", 409);
            this.factory.assertFresh(featureId, execution.id);
            await this.fastForwardLocalDeliveryTargets(featureId, execution);
            const pins = await sourcePins(this.factory);
            if (this.stopping) throw new FactoryError("runtime_stopping", "The host stopped before workflow admission. No agents were launched; explicitly start again when ready.", 409);
            this.factory.assertFresh(featureId, execution.id);
            this.factory.store.updateExecution(featureId, execution.id, { sourcePins: pins, phase: "Starting Copilot session" });
            await this.run(featureId, this.factory.store.execution(featureId, execution.id));
        })().catch(async (error: unknown) => {
            this.fail(featureId, execution.id, error);
            const failed = this.factory.store.execution(featureId, execution.id);
            if (failed.status === "error" && this.factory.store.ownsSession(failed.sessionId, this.ownerToken)) {
                if (!failed.runtimeId && !failed.admissionClaim) {
                    await this.shared?.releaseUnstartedRun(featureId, execution.id).catch(() => {});
                } else {
                    await this.shared?.releaseFailedRun(featureId, execution.id).catch(() => {});
                }
            }
        });
        this.pending.add(pending);
        void pending.finally(() => { this.pending.delete(pending); this.launching.delete(execution.id); });
    }

    private async fastForwardLocalDeliveryTargets(featureId: string, execution: Execution): Promise<void> {
        if (execution.kind !== "implement" || !this.factory.provider.fetchRepairRefs) return;
        try {
            const config = this.factory.configuration();
            const delivery = deliverySettings(config, execution.delivery);
            if (delivery.mode !== "local") return;
            const { scope } = this.factory.requireDispatchDecision(featureId, delivery);
            const eligible = Array.isArray(scope.eligible) ? scope.eligible : [];
            const seenRepos = new Set<string>();
            for (const raw of eligible) {
                if (!raw || typeof raw !== "object" || Array.isArray(raw) || typeof (raw as Record<string, unknown>).repository !== "string") continue;
                const repoKey = String((raw as Record<string, unknown>).repository);
                if (seenRepos.has(repoKey) || !config.repositories[repoKey]) continue;
                seenRepos.add(repoKey);
                const repo = config.repositories[repoKey]!;
                const repoRoot = await ensureSubrepoWorktree(this.factory.store.registry.project.workspace, repo.localPath, repo.baseBranch, systemRunner);
                const [statusOut, branchOut, headSha, oldOriginSha] = await Promise.all([
                    systemRunner.run("git", ["status", "--porcelain"], { cwd: repoRoot }),
                    systemRunner.run("git", ["symbolic-ref", "--quiet", "--short", "HEAD"], { cwd: repoRoot }).catch(() => ""),
                    systemRunner.run("git", ["rev-parse", "HEAD"], { cwd: repoRoot }),
                    systemRunner.run("git", ["rev-parse", `refs/remotes/origin/${repo.baseBranch}`], { cwd: repoRoot }).catch(() => ""),
                ]);
                if (!statusOut.trim() && branchOut.trim() === repo.baseBranch && headSha === oldOriginSha) {
                    await this.factory.provider.fetchRepairRefs(repo, repoRoot, [repo.baseBranch]);
                    const newOriginSha = await systemRunner.run("git", ["rev-parse", `refs/remotes/origin/${repo.baseBranch}`], { cwd: repoRoot });
                    if (newOriginSha !== headSha) {
                        await systemRunner.run("git", ["merge", "--ff-only", `refs/remotes/origin/${repo.baseBranch}`], { cwd: repoRoot });
                    }
                }
            }
        } catch {
            // Best-effort fast-forward; verifyLocalReadiness remains the authoritative gate inside implementRun.
        }
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
        this.heartbeats.set(execution.sessionId, Date.now());
        const { createClient: makeClient, ownedSessionHostOptions, sessionConfiguration } = await import("./host.ts");
        if (!this.started) {
            requireRuntime();
            this.client ??= makeClient(this.factory.store.registry.project.workspace, path.dirname(this.factory.store.registry.databasePath));
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
        let publishedSessionUri: string | null = null;
        let publicationError: string | null = null;
        const hosting = ownedSessionHostOptions(execution.sessionId);
        try {
            let host = this.ahpHosts.get(execution.sessionId);
            if (!host) {
                host = await this.client!.startAhpHost(hosting);
                this.ahpHosts.set(execution.sessionId, host);
            }
            const published = await host.publishSession(session.sessionId);
            if (published.sessionId !== execution.sessionId) throw new FactoryError("wrong_workflow_owner", "Host publication returned another session.", 409);
            publishedSessionUri = published.sessionUri;
        } catch {
            publicationError = "App-host publication is unavailable. Workflow ownership is unchanged; use the owning Factory runtime and inspect host authentication before retrying. No app attachment has been verified.";
        }
        this.sessions.set(execution.id, session);
        for (const feature of this.factory.store.registry.list()) {
            if (this.factory.store.state(feature.id).sessionBinding?.sessionId === execution.sessionId) {
                this.factory.store.change(feature.id, (state) => {
                    if (state.sessionBinding) {
                        state.sessionBinding.established = true;
                        state.sessionBinding.publishedSessionUri = publishedSessionUri;
                        state.sessionBinding.hostComputeId = hosting.computeId;
                        state.sessionBinding.publicationError = publicationError;
                    }
                }, { localOnly: true });
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
            intake: "software-factory-intake", design: "software-factory-design", plan: "software-factory-plan",
            implement: "software-factory-implement", lifecycle: "software-factory-lifecycle",
            monitor: "software-factory-monitor", check: "software-factory-check",
        };
        await this.registration(featureId, execution, session);
        if (execution.kind !== "check") await this.shared?.assertRun(featureId, execution.id);
        this.factory.store.change(featureId, (state) => {
            const current = state.executions.find((item) => item.id === execution.id)!;
            if (current.admissionClaim || current.runtimeId) throw new FactoryError("native_admission_exists", "This execution was already submitted or its admission is uncertain. Inspect the owning run; do not submit it twice.", 409);
            current.admissionClaim = this.ownerToken;
        }, { localOnly: true });
        const result = await session.workflow.run(names[execution.kind], {
            args: { featureId, executionId: execution.id }, notifyOnComplete: execution.sessionKind === "chat" && execution.kind !== "check", logPhaseNames: true,
            limits: { maxAiCredits: null, ...execution.limits },
        });
        this.settle(featureId, execution.id, result);
        await this.observeUsage(featureId, execution.id, session, result.runId);
        if (this.factory.store.execution(featureId, execution.id).status === "error" &&
            this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) {
            await this.shared?.releaseFailedRun(featureId, execution.id).catch(() => {});
        }
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
            if (this.factory.store.execution(featureId, executionId).status === "error" &&
                this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) {
                await this.shared?.releaseFailedRun(featureId, executionId).catch(() => {});
            }
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
            if (execution.kind !== "check") await this.shared?.assertRun(featureId, executionId);
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
        }, { localOnly: true });
        await this.poll();
        return { requestId: id, status: "queued", executionId, action };
    }

    private async poll(): Promise<void> {
        if (this.polling || this.stopping) return;
        this.polling = true;
        try {
            const nowMs = Date.now();
            for (const id of this.leaseSessions) {
                if (nowMs - (this.heartbeats.get(id) ?? 0) >= 5_000 || !this.factory.store.ownsSession(id, this.ownerToken)) {
                    this.factory.store.heartbeat(id, this.ownerToken);
                    this.heartbeats.set(id, nowMs);
                }
            }
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
                    }, { localOnly: true });
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
                                if (execution.kind !== "check") await this.shared?.assertRun(feature.id, execution.id);
                                this.factory.store.updateExecution(feature.id, execution.id, { status: "running", error: null, errorCode: null });
                                this.markControl(feature.id, control.id);
                                const currentApproved = execution.usage?.approvedLimits ?? {};
                                const effectiveLimits = Object.fromEntries(
                                    Object.entries(control.limits).filter(([key, value]) => value === null || currentApproved[key] !== value),
                                );
                                const result = await session.workflow.resume(execution.runtimeId, {
                                    ...(Object.keys(effectiveLimits).length ? { limits: effectiveLimits } : {}),
                                    notifyOnComplete: execution.sessionKind === "chat" && execution.kind !== "check",
                                    logPhaseNames: true,
                                });
                                this.settle(feature.id, execution.id, result);
                                await this.observeUsage(feature.id, execution.id, session, result.runId);
                                if (this.factory.store.execution(feature.id, execution.id).status === "error" &&
                                    this.factory.store.ownsSession(execution.sessionId, this.ownerToken)) {
                                    await this.shared?.releaseFailedRun(feature.id, execution.id).catch(() => {});
                                }
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
        }, { localOnly: true });
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
        const hostErrors: Error[] = [];
        for (const host of this.ahpHosts.values()) {
            try { await host.dispose(); }
            catch { hostErrors.push(new Error("A Factory session host did not acknowledge shutdown.")); }
        }
        this.ahpHosts.clear();
        const errors = await this.client?.stop() || [];
        errors.push(...hostErrors);
        await Promise.allSettled([...this.pending]);
        if (this.client) errors.push(...await this.client.stop());
        for (const id of this.leaseSessions) this.factory.store.releaseOwner(id, this.ownerToken);
        if (errors.length) throw new AggregateError(errors, "Workflow runtime cleanup failed; inspect interrupted runs before recovery.");
    }
}
