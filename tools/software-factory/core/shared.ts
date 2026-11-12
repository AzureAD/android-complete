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

interface CompletionGuard {
    executionId: string;
    previous: SharedRecord;
    output: SharedSnapshot;
}

export class SharedAuthority {
    private readonly app: Application;
    readonly provider: SharedProvider;
    constructor(app: Application, provider: SharedProvider = new AdoFeatureProvider()) {
        this.app = app;
        this.provider = provider;
        app.registry.connection.exec(`CREATE TABLE IF NOT EXISTS shared_features (
            feature_id TEXT PRIMARY KEY, target_json TEXT NOT NULL, record_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shared_completion_guards (
            feature_id TEXT PRIMARY KEY, guard_json TEXT NOT NULL
        )`);
    }

    private completionGuard(featureId: string): CompletionGuard | null {
        const row = this.app.registry.connection.prepare("SELECT guard_json FROM shared_completion_guards WHERE feature_id=?").get(featureId);
        if (!row) return null;
        const raw = object(JSON.parse(String(row.guard_json)), "Pending shared completion");
        const previous = object(raw.previous, "Previous shared record");
        const snapshot = parseSnapshot(previous.snapshot);
        const output = parseSnapshot(raw.output);
        if (snapshot.feature.id !== featureId || output.feature.id !== featureId ||
            previous.digest !== digest(snapshot) || !Number.isSafeInteger(previous.revision) ||
            typeof raw.executionId !== "string" || !raw.executionId) {
            throw new FactoryError("shared_corrupt", "Invalid pending shared completion.", 409);
        }
        return {
            executionId: raw.executionId,
            previous: { target: sharedTarget(previous.target), revision: Number(previous.revision), digest: String(previous.digest), snapshot },
            output,
        };
    }

    private saveCompletionGuard(featureId: string, guard: CompletionGuard): void {
        this.app.registry.connection.prepare("INSERT INTO shared_completion_guards VALUES (?, ?) ON CONFLICT(feature_id) DO UPDATE SET guard_json=excluded.guard_json")
            .run(featureId, JSON.stringify(guard));
    }

    private clearCompletionGuard(featureId: string): void {
        this.app.registry.connection.prepare("DELETE FROM shared_completion_guards WHERE feature_id=?").run(featureId);
    }

    assertNoCompletionConflict(featureId: string): void {
        if (this.completionGuard(featureId)) {
            throw new FactoryError("shared_completion_reconciliation_required",
                "A shared completion may have been published while local state changed or its response was lost. Review and reconcile it before continuing.", 409);
        }
    }

    completionScope(featureId: string): Json {
        const guard = this.completionGuard(featureId);
        if (!guard) throw new FactoryError("shared_completion_not_pending", "There is no pending shared completion to reconcile.", 409);
        return jsonValue({
            featureId, executionId: guard.executionId, target: guard.previous.target,
            previousDigest: guard.previous.digest, previousRevision: guard.previous.revision,
            attempted: guard.output, local: this.snapshot(this.app, featureId),
            effect: "Preserve the reviewed local state only if the shared record still matches the attempted completion or its original claim. No coding or provider dispatch is retried.",
        });
    }

    private async reconcileCompletion(featureId: string, approvedLocalDigest: string): Promise<void> {
        const guard = this.completionGuard(featureId);
        if (!guard) throw new FactoryError("shared_completion_not_pending", "There is no pending shared completion to reconcile.", 409);
        const current = await this.provider.read(guard.previous.target);
        if (!current || current.snapshot.feature.id !== featureId || digest(current.target) !== digest(guard.previous.target)) {
            throw new FactoryError("shared_binding_changed", "The shared feature binding changed; no local state was published.", 409);
        }
        const matchesAttempt = current.digest === digest(guard.output);
        const matchesPrevious = current.digest === guard.previous.digest && current.revision === guard.previous.revision;
        if (!matchesAttempt && !matchesPrevious) {
            throw new FactoryError("shared_conflict", "The shared record changed independently. Preserve both versions and resolve the conflict before retrying.", 409);
        }
        const local = this.snapshot(this.app, featureId);
        if (digest(local) !== approvedLocalDigest) {
            throw new FactoryError("stale_scope", "Local recovery evidence changed after review. Review it again before reconciling.", 409);
        }
        if (matchesAttempt && digest(local) === current.digest) {
            this.save(current);
            this.clearCompletionGuard(featureId);
            return;
        }
        const actor = await this.provider.identity(current.target);
        if (digest(this.snapshot(this.app, featureId)) !== approvedLocalDigest) {
            throw new FactoryError("stale_scope", "Local recovery evidence changed after review. Review it again before reconciling.", 409);
        }
        this.saveCompletionGuard(featureId, { executionId: guard.executionId, previous: current, output: local });
        const saved = await this.provider.write(current.target, current, local, actor);
        this.save(saved);
        if (digest(this.snapshot(this.app, featureId)) !== approvedLocalDigest) {
            throw new FactoryError("shared_local_state_changed", "Local recovery evidence changed during reconciliation; preserve it and review again.", 409);
        }
        this.clearCompletionGuard(featureId);
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

    private hydrate(app: Application, record: SharedRecord, readOnly = false): void {
        const snapshot = parseSnapshot(record.snapshot);
        const current = this.app.lifecycle.store.configuration();
        const matches = current && digest(sharedConfiguration(current.value)) === digest(snapshot.configuration.value);
        if (!matches && !readOnly) {
            throw new FactoryError("shared_mapping_required", "Configure matching repository/branch/board targets locally before opening this team feature. Local checkout paths and GitHub accounts may differ.", 409);
        }
        const db = app.registry.connection;
        if (matches) {
            const config = snapshot.configuration;
            const localRepositories = (repositories: typeof config.value.repositories, mappings: typeof config.value.repositories) =>
                Object.fromEntries(Object.entries(repositories).map(([key, repo]) =>
                    [key, { ...repo, account: mappings[key]!.account, localPath: mappings[key]!.localPath }]));
            const localValue = { ...config.value, repositories: localRepositories(config.value.repositories, current.value.repositories),
                ...(config.value.referenceRepositories ? {
                    referenceRepositories: localRepositories(config.value.referenceRepositories, current.value.referenceRepositories || {}),
                } : {}) };
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

    private preserveOwnedRun(record: SharedRecord): boolean {
        const featureId = record.snapshot.feature.id;
        const cached = this.cached(featureId);
        if (!cached) return false;
        if (digest(cached.target) !== digest(record.target)) {
            throw new FactoryError("shared_binding_changed", "This feature is already bound to another shared target.", 409);
        }
        const ownedRun = cached.snapshot.lock && this.app.lifecycle.store.state(featureId).executions.some((entry) =>
            entry.id === cached.snapshot.lock!.id && entry.status !== "completed" && entry.status !== "superseded");
        if (!ownedRun) return false;
        if (record.digest !== cached.digest || record.snapshot.lock?.id !== cached.snapshot.lock?.id) {
            throw new FactoryError("shared_run_revoked", "The shared run claim or approved inputs changed. No result or downstream action was accepted.", 409);
        }
        return true;
    }

    async pull(featureId: string, readOnly = false): Promise<SharedRecord | null> {
        this.assertNoCompletionConflict(featureId);
        const cached = this.cached(featureId);
        if (!cached) return null;
        const record = await this.provider.read(cached.target);
        if (!record || record.snapshot.feature.id !== featureId) throw new FactoryError("shared_binding_changed", "The ADO feature binding was removed or replaced. No cached approval can advance work.", 409);
        if (this.preserveOwnedRun(record)) return record;
        this.hydrate(this.app, record, readOnly);
        this.save(record);
        return record;
    }

    configurationMismatch(featureId: string) {
        const record = this.cached(featureId);
        const config = this.app.lifecycle.store.configuration();
        if (!record || (config && digest(sharedConfiguration(config.value)) === digest(record.snapshot.configuration.value))) return null;
        return {
            code: "shared_mapping_required" as const,
            message: "Local connections differ from this feature's shared configuration. History remains readable, but new work is blocked. Review a shared reference-source update or restore matching local connections; saving local connections did not update ADO.",
            sharedConfigurationId: record.snapshot.configuration.id,
            localConfigurationId: config?.id || null,
            command: { kind: "update_shared_references" as const, featureId },
        };
    }

    referenceUpdateScope(featureId: string): Json {
        this.assertNoCompletionConflict(featureId);
        const record = this.cached(featureId);
        const config = this.app.lifecycle.store.configuration();
        if (!record || !config) throw new FactoryError("shared_mapping_required", "Read the shared feature and configure local connections before reviewing its reference sources.", 409);
        const before = record.snapshot.configuration.value;
        const after = sharedConfiguration(config.value);
        const delivery = (value: typeof before) => ({ repositories: value.repositories, boards: value.boards, delivery: deliverySettings(value) });
        if (digest(delivery(before)) !== digest(delivery(after))) throw new FactoryError("shared_target_changed", "This action can change only read-only reference sources. Restore matching delivery targets, boards and coding settings first.", 409);
        if (digest(before.referenceRepositories || {}) === digest(after.referenceRepositories || {})) {
            throw new FactoryError("shared_references_unchanged", "The read-only reference sources already match; no shared update is needed.", 409);
        }
        if (record.snapshot.lock || this.app.lifecycle.store.state(featureId).executions.some((run) => ["starting", "running", "paused"].includes(run.status)) ||
            record.snapshot.state.operations.some((operation) => ["inflight", "reconciliation_required"].includes(operation.status))) {
            throw new FactoryError("shared_busy", "Resolve active shared claims, runs and uncertain provider operations before changing reference sources.", 409);
        }
        return jsonValue({
            featureId, target: record.target, sharedRevision: record.revision, sharedDigest: record.digest,
            previousConfiguration: before, configuration: after, localConfigurationId: config.id,
            effect: "Update only this ADO feature's read-only reference-source scope. Preserve requirements, approval history, failed runs and artifacts. Prior stage artifacts and approvals do not approve the new source scope. No workflow, backlog, coding or merge starts.",
        });
    }

    private async updateReferences(featureId: string, actor: Actor, consent?: Consent): Promise<Record<string, Json>> {
        await this.pull(featureId, true);
        const scope = this.referenceUpdateScope(featureId);
        if (!consent || !consent.granted || consent.revisionId !== hash(scope) || digest(consent.actor) !== digest(actor)) {
            throw new FactoryError("consent_required", "Confirm the current exact shared reference-source update; local connection approval is not shared publication approval.", 403);
        }
        const record = this.cached(featureId)!;
        const identity = await this.provider.identity(record.target);
        if (digest(this.referenceUpdateScope(featureId)) !== digest(scope)) throw new FactoryError("stale_scope", "Shared reference update scope changed during identity verification.", 409);
        const config = this.app.lifecycle.store.configuration()!;
        const output: SharedSnapshot = {
            ...record.snapshot,
            configuration: { ...config, value: sharedConfiguration(config.value), hash: digest(sharedConfiguration(config.value)) },
        };
        // Reuse completion recovery for a lost CAS response; never blindly repeat a publication.
        this.saveCompletionGuard(featureId, { executionId: `references:${config.id}`, previous: record, output });
        let saved: SharedRecord;
        try { saved = await this.provider.write(record.target, record, output, identity); }
        catch (error) {
            throw new FactoryError("shared_reference_update_incomplete",
                `${error instanceof Error ? error.message : "The shared publication failed."} A recovery record was preserved; inspect and reconcile it before retrying. No workflow was started.`, 409);
        }
        this.save(saved);
        if (digest(this.app.lifecycle.store.configuration()?.value) !== digest(config.value)) {
            throw new FactoryError("shared_local_state_changed", "Local connections changed while the shared update was published. Review completion recovery before continuing.", 409);
        }
        this.hydrate(this.app, saved);
        this.clearCompletionGuard(featureId);
        return { outcome: "shared_references_updated", feature: jsonValue(this.app.cachedFeature(featureId)) };
    }

    async open(target: SharedTarget) {
        const record = await this.provider.read(sharedTarget(target));
        if (!record) throw new FactoryError("shared_not_found", "This ADO Feature has no published Software Factory record.", 404);
        this.assertNoCompletionConflict(record.snapshot.feature.id);
        if (this.preserveOwnedRun(record)) return this.app.lifecycle.store.view(record.snapshot.feature.id);
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
        this.assertNoCompletionConflict(featureId);
        const cached = this.cached(featureId);
        if (!cached) return;
        const current = await this.provider.read(cached.target);
        if (!current || current.snapshot.lock?.id !== executionId || current.digest !== cached.digest) {
            throw new FactoryError("shared_run_revoked", "The shared run claim or approved inputs changed. No result or downstream action was accepted.", 409);
        }
        this.save(current);
    }

    async checkpointRun(featureId: string, executionId: string): Promise<void> {
        const cached = this.cached(featureId);
        if (!cached) return;
        await this.assertRun(featureId, executionId);
        const current = this.cached(featureId)!;
        const actor = await this.provider.identity(current.target);
        const saved = await this.provider.write(current.target, current,
            this.snapshot(this.app, featureId, current.snapshot.lock), actor);
        this.save(saved);
    }

    async finishRun(featureId: string, executionId: string): Promise<void> {
        this.assertNoCompletionConflict(featureId);
        const cached = this.cached(featureId);
        if (!cached) return;
        const output = this.snapshot(this.app, featureId);
        await this.assertRun(featureId, executionId);
        const actor = await this.provider.identity(cached.target);
        if (digest(this.snapshot(this.app, featureId)) !== digest(output)) {
            throw new FactoryError("shared_local_state_changed", "Local workflow state changed while completing the shared run; checkpoint it before retrying.", 409);
        }
        const previous = this.cached(featureId)!;
        this.saveCompletionGuard(featureId, { executionId, previous, output });
        const saved = await this.provider.write(cached.target, previous, output, actor);
        this.save(saved);
        if (digest(this.snapshot(this.app, featureId)) !== digest(output)) {
            throw new FactoryError("shared_local_state_changed", "Local workflow state changed during completion; preserve it and reconcile before continuing.", 409);
        }
        this.clearCompletionGuard(featureId);
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
        if (command.kind === "update_shared_references" && id) return this.updateReferences(id, actor, consent);
        if (command.kind === "reconcile_shared_completion" && id) {
            const preview = prepare(this.app, input);
            if (!consent || consent.granted !== true || consent.revisionId !== preview.consentKey ||
                digest(consent.actor) !== digest(actor)) throw new FactoryError("consent_required", "Confirm the current recovery evidence before reconciling.", 403);
            const scope = object(preview.scope, "Recovery scope");
            await this.reconcileCompletion(id, digest(scope.local));
            return { feature: jsonValue(this.app.lifecycle.store.view(id)) };
        }
        const record = id ? await this.pull(id, command.kind === "report_progress") : null;
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
