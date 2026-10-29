// Copyright (c) Microsoft Corporation. All rights reserved.

import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import path from "node:path";
import { Application } from "./application.ts";
import { FactoryError, object, type Actor, type Consent } from "./contracts.ts";
import { dispatch, prepare } from "./commands.ts";
import { hash, now } from "./lifecycle-store.ts";
import { deliverySettings, jsonValue, parseConfiguration, sharedConfiguration, type Execution, type Json } from "./lifecycle-contracts.ts";
import { AdoFeatureProvider } from "../providers/ado-features.ts";
import { digest, parseSnapshot, publishTarget, sharedTarget, type PublishTarget, type SharedProvider, type SharedRecord, type SharedSnapshot, type SharedTarget } from "./shared-contracts.ts";

const localCommands = new Set(["report_progress", "control_run", "set_schedule", "supersede_execution", "export_feature", "review_pr"]);

export class SharedAuthority {
    private readonly app: Application;
    readonly provider: SharedProvider;
    constructor(app: Application, provider: SharedProvider = new AdoFeatureProvider()) {
        this.app = app;
        this.provider = provider;
        app.registry.connection.exec(`CREATE TABLE IF NOT EXISTS shared_features (
            feature_id TEXT PRIMARY KEY, target_json TEXT NOT NULL, record_json TEXT NOT NULL
        )`);
    }

    cached(featureId: string): SharedRecord | null {
        const row = this.app.registry.connection.prepare("SELECT record_json FROM shared_features WHERE feature_id=?").get(featureId);
        if (!row) return null;
        const raw = object(JSON.parse(String(row.record_json)), "Cached shared record");
        const snapshot = parseSnapshot(raw.snapshot);
        if (snapshot.feature.id !== featureId || raw.digest !== digest(snapshot) || !Number.isSafeInteger(raw.revision)) throw new FactoryError("shared_corrupt", "Invalid shared cache.", 409);
        return { snapshot, digest: String(raw.digest), revision: Number(raw.revision), target: sharedTarget(raw.target) };
    }

    private save(record: SharedRecord): void {
        const serialized = JSON.stringify(record);
        const existing = this.app.registry.connection.prepare("SELECT record_json FROM shared_features WHERE feature_id=?").get(record.snapshot.feature.id);
        if (existing && existing.record_json === serialized) return;
        this.app.registry.connection.prepare("INSERT INTO shared_features VALUES (?, ?, ?) ON CONFLICT(feature_id) DO UPDATE SET record_json=excluded.record_json")
            .run(record.snapshot.feature.id, JSON.stringify(record.target), serialized);
    }

    private hydrate(app: Application, record: SharedRecord): void {
        const snapshot = parseSnapshot(record.snapshot);
        const current = this.app.lifecycle.store.configuration();
        if (!current || digest(sharedConfiguration(current.value)) !== digest(snapshot.configuration.value)) {
            throw new FactoryError("shared_mapping_required", "Configure matching repository/branch/board targets locally before opening this team feature. Local checkout paths and GitHub accounts may differ.", 409);
        }
        const config = snapshot.configuration;
        const localValue = { ...config.value, repositories: Object.fromEntries(Object.entries(config.value.repositories).map(([key, repo]) =>
            [key, { ...repo, account: current.value.repositories[key]!.account, localPath: current.value.repositories[key]!.localPath }])) };
        const db = app.registry.connection;
        const prior = app.lifecycle.store.configuration(config.id);
        if (prior && digest(prior.value) !== digest(parseConfiguration(localValue))) throw new FactoryError("shared_mapping_changed", "Local mappings changed for a cached shared configuration. Reconcile the mapping before continuing this feature.", 409);
        if (!prior) db.prepare("INSERT INTO factory_configuration VALUES (?, ?, ?, ?, ?, ?)")
            .run(config.id, app.registry.project.id, hash(localValue), JSON.stringify(localValue),
                JSON.stringify({ channel: "web", reference: "shared-cache" }), config.createdAt);
        const serializedLocal = JSON.stringify(localValue);
        const existingMapping = db.prepare("SELECT configuration_id, mappings_json FROM factory_local_configuration WHERE project_id=?").get(app.registry.project.id);
        if (!existingMapping || existingMapping.configuration_id !== config.id || existingMapping.mappings_json !== serializedLocal) {
            db.prepare("INSERT INTO factory_local_configuration VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET configuration_id=excluded.configuration_id, mappings_json=excluded.mappings_json")
                .run(app.registry.project.id, config.id, serializedLocal);
        }
        app.registry.importShared(snapshot.feature);
        const local = app.lifecycle.store.state(snapshot.feature.id);
        const state = { ...snapshot.state, executions: local.executions, sessionBinding: local.sessionBinding || null,
            controls: local.controls, schedule: local.schedule, progress: local.progress,
            events: [...snapshot.state.events, ...local.events.filter((entry) => entry.kind.startsWith("workflow_"))] };
        const serializedState = JSON.stringify(state);
        const existingState = db.prepare("SELECT content_json FROM factory_state WHERE feature_id=?").get(snapshot.feature.id);
        if (!existingState || existingState.content_json !== serializedState) {
            db.prepare("INSERT INTO factory_state VALUES (?, 1, ?) ON CONFLICT(feature_id) DO UPDATE SET content_json=excluded.content_json, version=version+1")
                .run(snapshot.feature.id, serializedState);
        }
    }

    snapshot(app: Application, featureId: string, lock: SharedSnapshot["lock"] = null): SharedSnapshot {
        const feature = app.registry.get(featureId);
        const config = app.lifecycle.store.configuration();
        if (!config) throw new FactoryError("configuration_required", "Configure target repositories and ADO boards before publishing.", 409);
        const state = app.lifecycle.store.state(featureId);
        return {
            version: 1,
            feature: { ...feature, projectId: "ado", audit: feature.audit.map((entry, index) => ({ ...entry, sequence: index + 1 })) },
            state: { ...state, executions: [], sessionBinding: null, controls: [], schedule: null, progress: [],
                events: state.events.filter((entry) => !entry.kind.startsWith("workflow_")) },
            configuration: { ...config, value: sharedConfiguration(config.value), hash: digest(sharedConfiguration(config.value)) },
            lock,
        };
    }

    async pull(featureId: string): Promise<SharedRecord | null> {
        const cached = this.cached(featureId);
        if (!cached) return null;
        const record = await this.provider.read(cached.target);
        if (!record || record.snapshot.feature.id !== featureId) throw new FactoryError("shared_binding_changed", "The ADO feature binding was removed or replaced. No cached approval can advance work.", 409);
        this.hydrate(this.app, record);
        this.save(record);
        return record;
    }

    async open(target: SharedTarget) {
        const record = await this.provider.read(sharedTarget(target));
        if (!record) throw new FactoryError("shared_not_found", "This ADO Feature has no published Software Factory record.", 404);
        this.hydrate(this.app, record);
        this.save(record);
        return this.app.lifecycle.store.view(record.snapshot.feature.id);
    }

    async publish(featureId: string, requestedTarget: PublishTarget, actor: Actor, options: { approve?: boolean } = {}) {
        const source = this.app.registry.get(featureId);
        if (options.approve) {
            if (!source.current.requirements.length) throw new FactoryError("success_criteria_required", "Add and review at least one success criterion before approving this feature for design.", 409);
            if (source.current.blockingUnknowns.length) throw new FactoryError("blocking_unknowns", "Resolve the scope decisions before approving for design.", 409);
        }
        const boards = this.app.lifecycle.store.configuration()?.value.boards;
        const parsedTarget = publishTarget(requestedTarget, boards);
        const target = "create" in parsedTarget
            ? await (() => {
                if (!this.provider.createFeature) throw new FactoryError("feature_creation_unsupported", "This shared provider does not support creating ADO Features.", 501);
                return this.provider.createFeature(parsedTarget, source.current.title, source.current.description);
            })()
            : parsedTarget;
        const identity = await this.provider.identity(target);
        const trustedActor = { ...actor, ado: identity };
        // A successor preserves local historical approvals without importing them as team consent.
        const successor = this.app.registry.create(`publish:${featureId}:${digest(target)}:${source.current.id}`, {
            title: source.current.title, ...(source.current.description ? { description: source.current.description } : {}),
            ...(source.current.designTasks?.length ? { designTasks: source.current.designTasks } : {}),
            requirements: source.current.requirements, blockingUnknowns: source.current.blockingUnknowns,
        }, trustedActor);
        const existing = await this.provider.read(target);
        if (existing && existing.snapshot.feature.id !== successor.id) throw new FactoryError("shared_target_in_use", "That ADO Feature already belongs to another shared record.", 409);
        let record = existing || await this.provider.write(target, null, this.snapshot(this.app, successor.id), identity);
        this.hydrate(this.app, record);
        this.save(record);
        if (options.approve && this.app.registry.get(successor.id).status !== "ready_for_design") {
            this.app.registry.approve(successor.id, successor.current.id, trustedActor);
            record = await this.provider.write(target, record, this.snapshot(this.app, successor.id), identity);
            this.hydrate(this.app, record);
            this.save(record);
        }
        return this.app.lifecycle.store.view(successor.id);
    }

    async claimRun(featureId: string, execution: Execution): Promise<void> {
        const current = await this.pull(featureId);
        if (!current) throw new FactoryError("publish_required", "Publish the reviewed draft to an ADO Feature before starting shared stages.", 409);
        if (current.snapshot.lock) throw new FactoryError("shared_busy", "Another shared operation owns this feature. Inspect it; never take over an active run automatically.", 409);
        const actor = await this.provider.identity(current.target);
        const saved = await this.provider.write(current.target, current, { ...current.snapshot,
            lock: { id: execution.id, kind: execution.kind, actor, createdAt: now() } }, actor);
        this.save(saved);
    }

    async assertRun(featureId: string, executionId: string): Promise<void> {
        const cached = this.cached(featureId);
        if (!cached) return;
        const current = await this.provider.read(cached.target);
        if (!current || current.snapshot.lock?.id !== executionId || current.digest !== cached.digest) {
            throw new FactoryError("shared_run_revoked", "The shared run claim or approved inputs changed. No result or downstream action was accepted.", 409);
        }
        this.save(current);
    }

    async finishRun(featureId: string, executionId: string): Promise<void> {
        const cached = this.cached(featureId);
        if (!cached) return;
        const output = this.snapshot(this.app, featureId);
        await this.assertRun(featureId, executionId);
        const actor = await this.provider.identity(cached.target);
        const saved = await this.provider.write(cached.target, this.cached(featureId)!, output, actor);
        this.hydrate(this.app, saved);
        this.save(saved);
    }

    async releaseUnstartedRun(featureId: string, executionId: string): Promise<void> {
        const cached = this.cached(featureId);
        if (!cached || cached.snapshot.lock?.id !== executionId) return;
        const execution = this.app.lifecycle.store.execution(featureId, executionId);
        if (execution.runtimeId || execution.admissionClaim) return;
        const current = await this.pull(featureId);
        if (!current || current.snapshot.lock?.id !== executionId) return;
        const identity = await this.provider.identity(current.target);
        const snapshot = structuredClone(current.snapshot);
        snapshot.lock = null;
        const baseActor: Actor = execution.requestedBy || { channel: "web", reference: executionId };
        snapshot.state.events.push({
            id: randomUUID(), kind: "shared_claim_released_unstarted", reference: executionId,
            createdAt: now(), message: "Automatically released shared claim after pre-admission startup failure; no workflow or agent was started.",
            actor: { ...baseActor, ado: identity },
        });
        const saved = await this.provider.write(current.target, current, snapshot, identity);
        this.hydrate(this.app, saved);
        this.save(saved);
    }

    async release(featureId: string, expectedClaimId: string, reason: string, actor: Actor): Promise<void> {
        const record = await this.pull(featureId);
        if (!record || record.snapshot.lock?.id !== expectedClaimId) throw new FactoryError("shared_claim_changed", "Review the current shared claim before releasing it.", 409);
        const identity = await this.provider.identity(record.target);
        const snapshot = structuredClone(record.snapshot);
        snapshot.lock = null;
        snapshot.state.events.push({ id: randomUUID(), kind: "shared_claim_released", reference: expectedClaimId,
            createdAt: now(), message: reason, actor: { ...actor, ado: identity } });
        const saved = await this.provider.write(record.target, record, snapshot, identity);
        this.hydrate(this.app, saved);
        this.save(saved);
    }

    async command(input: unknown, actor: Actor, consent?: Consent): Promise<Record<string, Json>> {
        const command = object(input, "Factory command");
        const id = typeof command.featureId === "string" ? command.featureId : null;
        const record = id ? await this.pull(id) : null;
        const isLocalDispatchWorkflow = command.kind === "dispatch_work" &&
            deliverySettings(this.app.lifecycle.store.configuration()?.value, {
                mode: command.mode, maxReviewRounds: command.maxReviewRounds,
            }).mode === "local";
        if (!record || !id || localCommands.has(String(command.kind)) || command.kind === "start_workflow" || isLocalDispatchWorkflow) {
            return dispatch(this.app, input, actor, consent);
        }
        const preview = prepare(this.app, input);
        if (preview.consentKey && (!consent || consent.revisionId !== preview.consentKey ||
            digest(consent.actor) !== digest(actor) || consent.granted !== true)) throw new FactoryError("consent_required", "Confirm the current exact shared scope before changing it.", 403);
        if (command.kind === "release_shared_claim") return dispatch(this.app, input, actor, consent);
        if (record.snapshot.lock) throw new FactoryError("shared_busy", "A shared run or operation is active. Stop/reconcile it before revising or approving this feature.", 409);
        const identity = await this.provider.identity(record.target);
        const trustedActor = { ...actor, ado: identity };
        const lock = { id: randomUUID(), kind: String(command.kind), actor: identity, createdAt: now() };
        let current = await this.provider.write(record.target, record, { ...record.snapshot, lock }, identity);
        this.save(current);
        const directory = mkdtempSync(path.join(path.dirname(this.app.registry.databasePath), "shared-attempt-"));
        const staged = new Application({ database: path.join(directory, "registry.sqlite"), workspace: this.app.registry.project.workspace,
            provider: this.app.lifecycle.provider, sharedProvider: this.provider });
        let completed = false;
        try {
            this.hydrate(staged, current);
            staged.lifecycle.checkpoint = async () => {
                current = await this.provider.write(record.target, current, this.snapshot(staged, id, lock), identity);
                this.save(current);
            };
            const stagedConsent = preview.consentKey ? { granted: true as const, revisionId: preview.consentKey, actor: trustedActor } : undefined;
            const result = await dispatch(staged, input, trustedActor, stagedConsent);
            current = await this.provider.write(record.target, current, this.snapshot(staged, id), identity);
            this.hydrate(this.app, current);
            this.save(current);
            completed = true;
            return { ...result, feature: jsonValue(this.app.lifecycle.store.view(id)) };
        } catch (error) {
            // A persisted claim is intentionally retained after failure, including uncertain writes.
            throw new FactoryError("shared_operation_incomplete",
                `${error instanceof Error ? error.message : "The operation failed."} Inspect the ADO claim and operation receipts before releasing or retrying. Local recovery evidence is retained at ${directory}.`, 409);
        } finally {
            staged.close();
            if (completed) rmSync(directory, { recursive: true, force: true });
        }
    }
}
