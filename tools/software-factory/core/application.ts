// Copyright (c) Microsoft Corporation. All rights reserved.

import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FactoryError, parseCommand, text, validateActor, type Actor, type Consent, type Feature } from "./contracts.ts";
import { Registry } from "./registry.ts";
import { Lifecycle } from "./lifecycle.ts";
import type { Provider } from "../providers/provider.ts";
import type { NativeRuntime, NativeSession } from "../runtime/controller.ts";
import type { DeliverySettings, Execution, ExecutionLimits, WorkflowAdmission, WorkflowKind } from "./lifecycle-contracts.ts";
import { dispatch, prepare } from "./commands.ts";
import { SharedAuthority } from "./shared.ts";
import type { SharedProvider } from "./shared-contracts.ts";
import { object } from "./contracts.ts";

export function defaultWorkspace(): string {
    return fileURLToPath(new URL("../../../", import.meta.url));
}

export function defaultDatabase(): string {
    const home = process.env.SOFTWARE_FACTORY_HOME ||
        path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), ".local", "share"), "SoftwareFactory");
    return path.join(home, "registry.sqlite");
}

export class Application {
    readonly registry: Registry;
    readonly lifecycle: Lifecycle;
    readonly shared: SharedAuthority;
    private native?: NativeRuntime;
    private joinedSession?: NativeSession;
    private closed = false;

    constructor(options: { database?: string; workspace?: string; provider?: Provider; sharedProvider?: SharedProvider } = {}) {
        this.registry = new Registry(options.database || defaultDatabase(), options.workspace || defaultWorkspace());
        this.lifecycle = new Lifecycle(this.registry, options.provider);
        this.shared = new SharedAuthority(this, options.sharedProvider);
    }

    execute(input: unknown, actor: Actor, consent?: Consent): Feature {
        const command = parseCommand(input);
        const trustedActor = validateActor(actor);
        switch (command.kind) {
            case "create_feature":
                return this.registry.create(command.requestId, command.input, trustedActor);
            case "revise_requirements":
            {
                const feature = this.registry.revise(command.featureId, command.expectedRevision, command.input, trustedActor);
                if (feature.current.id !== command.expectedRevision) {
                    this.lifecycle.store.change(feature.id, (state) => {
                        state.completed = null; state.schedule = null;
                        for (const run of state.executions) if (["starting", "running", "paused"].includes(run.status)) run.status = "superseded";
                    });
                }
                return this.registry.get(feature.id);
            }
            case "approve_requirements":
                if (!consent || consent.granted !== true || consent.revisionId !== command.expectedRevision ||
                    JSON.stringify(validateActor(consent.actor)) !== JSON.stringify(trustedActor)) {
                    throw new FactoryError("consent_required", "Approval requires a genuine confirmation from the UI or current chat host.", 403);
                }
                return this.registry.approve(command.featureId, command.expectedRevision, trustedActor);
        }
    }

    close(): void {
        if (!this.closed) { this.registry.close(); this.closed = true; }
    }

    prepare(input: unknown) { return prepare(this, input); }
    async dispatch(input: unknown, actor: Actor, consent?: Consent) { return dispatch(this, input, actor, consent); }

    async preview(input: unknown) {
        const command = object(input, "Factory command");
        if (typeof command.featureId === "string") {
            const shared = await this.shared.pull(command.featureId);
            if (!shared && (["approve_requirements", "approve_stage", "create_backlog", "dispatch_work", "post_pr_feedback",
                "reconcile_operation", "confirm_operation_not_sent", "set_schedule", "accept_validation_gap"].includes(String(command.kind)) ||
                (command.kind === "start_workflow" && command.workflow !== "intake") ||
                (command.kind === "control_run" && command.action === "resume" &&
                    !["intake", "check"].includes(this.lifecycle.store.execution(command.featureId, text(command.executionId, "Execution ID")).kind)))) {
                throw new FactoryError("publish_required", "This is a local draft. Publish it to an ADO Feature before approving or advancing shared stages. Historical local approvals are not team approvals.", 409);
            }
        }
        return prepare(this, input);
    }

    async submit(input: unknown, actor: Actor, consent?: Consent) {
        await this.preview(input);
        return this.shared.command(input, actor, consent);
    }

    async feature(featureId: string) {
        await this.shared.pull(featureId);
        return this.lifecycle.store.view(featureId);
    }

    async features() {
        const result = [];
        for (const feature of this.registry.list()) result.push(await this.feature(feature.id));
        return result;
    }

    executionOwner(featureId: string): { sessionId: string | null; kind: "chat" | "owned" } {
        const binding = this.lifecycle.store.state(featureId).sessionBinding;
        return binding ? { sessionId: binding.sessionId, kind: binding.kind }
            : this.joinedSession ? { sessionId: this.joinedSession.sessionId, kind: "chat" } : { sessionId: null, kind: "owned" };
    }

    async bindChatSession(session: NativeSession): Promise<void> {
        if (this.joinedSession && this.joinedSession.sessionId !== session.sessionId) throw new FactoryError("session_binding_changed", "A resident application cannot change chat owners.", 409);
        if (this.native && !this.joinedSession) throw new FactoryError("runtime_already_started", "Bind the chat before starting a runtime.", 409);
        this.joinedSession = session;
        const deadline = Date.now() + 16_000;
        while (true) {
            try { await this.runtime(); break; }
            catch (error) {
                if (!(error instanceof FactoryError) || error.code !== "session_owner_exists" || Date.now() >= deadline) throw error;
                await new Promise((resolve) => setTimeout(resolve, 200));
            }
        }
    }

    async runtime(): Promise<NativeRuntime> {
        if (!this.native) {
            const { NativeRuntime } = await import("../runtime/controller.ts");
            this.native ??= new NativeRuntime(this.lifecycle, this.joinedSession, this.shared);
        }
        return this.native;
    }

    async startWorkflow(featureId: string, kind: WorkflowKind, actor: Actor, limits: ExecutionLimits,
        admission: WorkflowAdmission, delivery?: Partial<DeliverySettings>): Promise<Execution> {
        const execution = this.lifecycle.begin(featureId, kind, actor, {}, limits, admission,
            this.joinedSession ? { sessionId: this.joinedSession.sessionId, kind: "chat", established: true } : undefined, delivery);
        try {
            if (kind !== "check" && (kind !== "intake" || this.shared.cached(featureId))) await this.shared.claimRun(featureId, execution);
            (await this.runtime()).launch(featureId, execution);
        }
        catch (error) {
            this.lifecycle.store.updateExecution(featureId, execution.id, {
                status: "error", phase: "Could not initialize Copilot",
                errorCode: error instanceof FactoryError ? error.code : "runtime_initialization_failed",
                error: error instanceof Error ? error.message : "Runtime initialization failed. Your saved feature is unchanged.",
            });
        }
        return this.lifecycle.store.execution(featureId, execution.id);
    }

    async shutdown(): Promise<void> {
        try { await this.native?.close(); }
        finally { this.close(); }
    }
}
