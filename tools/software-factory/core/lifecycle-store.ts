// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import type { DatabaseSync } from "node:sqlite";
import { FactoryError, type Actor } from "./contracts.ts";
import type { Registry } from "./registry.ts";
import { currentChatProgress, featureActivity } from "./progress.ts";
import {
    analysisConfiguration, jsonValue, parseConfiguration, sharedConfiguration, type Artifact, type Configuration, type ConfigRevision,
    type Execution, type FactoryFeature, type FactoryState, type Json,
} from "./lifecycle-contracts.ts";

export const hash = (value: unknown) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
export const now = () => new Date().toISOString();
export const blankState = (): FactoryState => ({
    version: 2, artifacts: [], decisions: [], feedback: [], operations: [], executions: [], events: [],
    completed: null, cancelled: false, schedule: null, controls: [], prBindings: [], waivers: [], progress: [],
});

export function lifecycleSchema(db: DatabaseSync): void {
    db.exec(`
        CREATE TABLE IF NOT EXISTS factory_state (
            feature_id TEXT PRIMARY KEY REFERENCES features(id), version INTEGER NOT NULL, content_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS factory_configuration (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            hash TEXT NOT NULL, content_json TEXT NOT NULL, actor_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS factory_resident_owner (
            session_id TEXT PRIMARY KEY, token TEXT NOT NULL, heartbeat INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS factory_local_configuration (
            project_id TEXT PRIMARY KEY, configuration_id TEXT NOT NULL, mappings_json TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS factory_configuration_immutable_update
            BEFORE UPDATE ON factory_configuration BEGIN SELECT RAISE(ABORT, 'Immutable configuration history'); END;
        CREATE TRIGGER IF NOT EXISTS factory_configuration_immutable_delete
            BEFORE DELETE ON factory_configuration BEGIN SELECT RAISE(ABORT, 'Immutable configuration history'); END;
    `);
}

export class LifecycleStore {
    readonly registry: Registry;
    constructor(registry: Registry) { this.registry = registry; }

    heartbeat(sessionId: string, token: string): void {
        const result = this.registry.connection.prepare(`INSERT INTO factory_resident_owner VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET token=excluded.token, heartbeat=excluded.heartbeat
            WHERE token=excluded.token OR heartbeat < ?`).run(sessionId, token, Date.now(), Date.now() - 15_000);
        if (!result.changes) throw new FactoryError("session_owner_exists", "Another resident controller owns this chat session.", 409);
    }

    releaseOwner(sessionId: string, token: string): void {
        this.registry.connection.prepare("DELETE FROM factory_resident_owner WHERE session_id=? AND token=?").run(sessionId, token);
    }

    ownerOnline(sessionId: string): boolean {
        return !!this.registry.connection.prepare("SELECT 1 FROM factory_resident_owner WHERE session_id=? AND heartbeat>=?")
            .get(sessionId, Date.now() - 15_000);
    }

    ownsSession(sessionId: string, token: string): boolean {
        return !!this.registry.connection.prepare("SELECT 1 FROM factory_resident_owner WHERE session_id=? AND token=? AND heartbeat>=?")
            .get(sessionId, token, Date.now() - 15_000);
    }

    state(featureId: string): FactoryState {
        this.registry.get(featureId);
        const row = this.registry.connection.prepare("SELECT content_json FROM factory_state WHERE feature_id = ?").get(featureId);
        if (!row) return blankState();
        if (typeof row.content_json !== "string") throw new FactoryError("storage_corrupt", "Invalid lifecycle record.", 500);
        const value: unknown = JSON.parse(row.content_json);
        if (!value || typeof value !== "object" || !("version" in value) || (value.version !== 1 && value.version !== 2) ||
            !["artifacts", "decisions", "feedback", "operations", "executions", "events"].every((key) => key in value && Array.isArray(Reflect.get(value, key)))) {
            throw new FactoryError("storage_corrupt", "Unsupported or corrupt lifecycle data; restore from a backup.", 500);
        }
        const state = value as FactoryState;
        state.version = 2;
        state.schedule ??= null;
        state.controls ??= [];
        state.prBindings ??= [];
        state.waivers ??= [];
        state.progress ??= [];
        return state;
    }

    change<T>(featureId: string, action: (state: FactoryState) => T): T {
        const db = this.registry.connection;
        db.exec("BEGIN IMMEDIATE");
        try {
            const state = this.state(featureId);
            const result = action(state);
            db.prepare(`INSERT INTO factory_state VALUES (?, 1, ?)
                ON CONFLICT(feature_id) DO UPDATE SET version = version + 1, content_json = excluded.content_json`)
                .run(featureId, JSON.stringify(state));
            db.prepare("UPDATE features SET updated_at = ? WHERE id = ?").run(now(), featureId);
            db.exec("COMMIT");
            return result;
        } catch (error) { db.exec("ROLLBACK"); throw error; }
    }

    configuration(id?: string): ConfigRevision | null {
        const mapping = this.registry.connection.prepare("SELECT configuration_id, mappings_json FROM factory_local_configuration WHERE project_id=?").get(this.registry.project.id);
        id ||= typeof mapping?.configuration_id === "string" ? mapping.configuration_id : undefined;
        const row = id
            ? this.registry.connection.prepare("SELECT * FROM factory_configuration WHERE project_id = ? AND id = ?").get(this.registry.project.id, id)
            : this.registry.connection.prepare("SELECT * FROM factory_configuration WHERE project_id = ? ORDER BY rowid DESC LIMIT 1").get(this.registry.project.id);
        if (!row) return null;
        if (![row.id, row.hash, row.content_json, row.created_at].every((value) => typeof value === "string")) {
            throw new FactoryError("storage_corrupt", "Invalid configuration record.", 500);
        }
        const value = parseConfiguration(JSON.parse(String(row.content_json)));
        if (typeof mapping?.mappings_json === "string") {
            const local = parseConfiguration(JSON.parse(mapping.mappings_json));
            for (const [key, repo] of Object.entries(value.repositories)) {
                const match = local.repositories[key];
                if (match && match.hostname === repo.hostname && match.slug === repo.slug) {
                    repo.localPath = match.localPath; repo.account = match.account;
                }
            }
        }
        return {
            id: String(row.id), hash: String(row.hash), createdAt: String(row.created_at),
            value,
        };
    }

    configure(value: Configuration, actor: Actor): ConfigRevision {
        const existing = this.configuration();
        if (existing && hash(existing.value) === hash(value)) return existing;
        const config = { id: hash(sharedConfiguration(value)), hash: hash(sharedConfiguration(value)), value, createdAt: now() };
        this.registry.connection.prepare("INSERT OR IGNORE INTO factory_configuration VALUES (?, ?, ?, ?, ?, ?)")
            .run(config.id, this.registry.project.id, config.hash, JSON.stringify(value), JSON.stringify(actor), config.createdAt);
        this.registry.connection.prepare("INSERT INTO factory_local_configuration VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET configuration_id=excluded.configuration_id, mappings_json=excluded.mappings_json")
            .run(this.registry.project.id, config.id, JSON.stringify(value));
        return config;
    }

    generation(state: FactoryState, stage: "design" | "plan", revision: string): number {
        return state.feedback.filter((item) => item.stage === stage && item.revision === revision).length;
    }

    latest(featureId: string, kind: Artifact["kind"]): Artifact | null {
        const feature = this.registry.get(featureId);
        const state = this.state(featureId);
        const configurationId = this.configuration()?.id || null;
        const analysisHash = hash(analysisConfiguration(this.configuration()?.value || null));
        const candidates = state.artifacts.filter((item) => item.kind === kind && item.requirementsRevision === feature.current.id &&
            (kind === "monitor" ? item.configurationId === configurationId : item.analysisHash === analysisHash));
        if (kind === "design" || kind === "plan") {
            const generation = this.generation(state, kind, feature.current.id);
            const parent = kind === "plan" ? this.latest(featureId, "design")?.id : null;
            return candidates.filter((item) => item.generation === generation && item.parentId === parent).at(-1) || null;
        }
        if (kind === "monitor") {
            const parent = this.latest(featureId, "plan")?.id;
            return candidates.filter((item) => item.parentId === parent).at(-1) || null;
        }
        return candidates.at(-1) || null;
    }

    artifact(featureId: string, kind: Artifact["kind"], revision: string, content: unknown, parentId: string | null = null, executionId?: string): Artifact {
        const body = jsonValue(content);
        const artifact = this.change(featureId, (state) => {
            if (this.registry.get(featureId).current.id !== revision) throw new FactoryError("stale_revision", "Requirements changed during execution.", 409);
            const generation = kind === "design" || kind === "plan" ? this.generation(state, kind, revision) : 0;
            const configurationId = this.configuration()?.id || null;
            if (executionId) {
                const execution = state.executions.find((entry) => entry.id === executionId);
                if (!execution || ["cancelled", "superseded", "completed"].includes(execution.status) ||
                    execution.requirementsRevision !== revision || execution.configurationId !== configurationId ||
                    ((kind === "design" || kind === "plan") && execution.generation[kind] !== generation)) {
                    throw new FactoryError("artifact_authority_changed", "Execution authority changed before the artifact commit.", 409);
                }
            }
            const analysisHash = hash(analysisConfiguration(this.configuration()?.value || null));
            const existing = state.artifacts.find((item) => item.kind === kind && item.requirementsRevision === revision &&
                item.configurationId === configurationId && item.parentId === parentId && item.generation === generation && item.hash === hash(body));
            if (existing) return existing;
            const item: Artifact = { id: randomUUID(), kind, requirementsRevision: revision, configurationId, analysisHash,
                executionId: executionId || null, parentId, generation, content: body, hash: hash(body), createdAt: now() };
            state.artifacts.push(item);
            this.event(state, `${kind}_artifact_saved`, item.id);
            return item;
        });
        this.exportArtifact(artifact);
        return artifact;
    }

    exportArtifact(artifact: Artifact): string {
        const directory = path.join(path.dirname(this.registry.databasePath), "artifacts");
        mkdirSync(directory, { recursive: true, mode: 0o700 });
        const file = path.join(directory, `${artifact.id}.json`);
        const content = JSON.stringify(artifact, null, 2);
        if (existsSync(file)) {
            if (readFileSync(file, "utf8") !== content) throw new FactoryError("artifact_conflict", "An immutable exported artifact was modified.", 409);
        } else writeFileSync(file, content, { flag: "wx", mode: 0o600 });
        return file;
    }

    event(state: FactoryState, kind: string, reference: string, details?: { actor?: Actor; message?: string }): void {
        state.events.push({ id: randomUUID(), kind, reference, createdAt: now(), ...details });
    }

    execution(featureId: string, id: string): Execution {
        const execution = this.state(featureId).executions.find((item) => item.id === id);
        if (!execution) throw new FactoryError("execution_not_found", "Execution not found in this feature.", 404);
        return execution;
    }

    updateExecution(featureId: string, id: string, update: Partial<Execution>): Execution {
        return this.change(featureId, (state) => {
            const execution = state.executions.find((item) => item.id === id);
            if (!execution) throw new FactoryError("execution_not_found", "Execution not found.", 404);
            if (["superseded", "cancelled", "completed"].includes(execution.status) && update.status && update.status !== execution.status) {
                throw new FactoryError("execution_authority_closed", "A terminal or revoked execution cannot be reactivated by a late callback.", 409);
            }
            const changed = (update.status && update.status !== execution.status) || (update.phase && update.phase !== execution.phase);
            Object.assign(execution, update, { updatedAt: now() });
            if (changed) this.event(state, "workflow_progress", id, {
                actor: execution.requestedBy,
                message: `${execution.kind}: ${execution.status} - ${execution.error || execution.phase}`,
            });
            return execution;
        });
    }

    view(featureId: string): FactoryFeature {
        const feature = this.registry.get(featureId);
        const factory = this.state(featureId);
        const design = this.latest(featureId, "design");
        const plan = this.latest(featureId, "plan");
        const latestDecision = (stage: string, targetId: string) => factory.decisions.filter((item) => item.stage === stage && item.targetId === targetId).at(-1);
        let lifecycleStatus: string = feature.status;
        if (design) lifecycleStatus = latestDecision("design", design.id)?.approved ? "ready_for_planning" : "design_review";
        if (plan) lifecycleStatus = "plan_review";
        if (plan && factory.operations.some((item) => item.planId === plan.id && item.kind === "work_item" && item.status === "completed")) lifecycleStatus = "backlog_review";
        if (plan && factory.operations.some((item) => item.planId === plan.id && item.kind === "dispatch" && item.status === "completed")) lifecycleStatus = "monitoring";
        if (factory.executions.some((item) => ["starting", "running"].includes(item.status))) lifecycleStatus = "running";
        if (factory.completed?.revision === feature.current.id) lifecycleStatus = "completed";
        if (factory.cancelled) lifecycleStatus = "cancelled";
        return { ...feature, factory, lifecycleStatus,
            ...this.sharedSummary(featureId),
            sessionOwnerOnline: factory.sessionBinding?.kind === "chat" ? this.ownerOnline(factory.sessionBinding.sessionId) : null,
            activity: featureActivity(feature, factory), currentProgress: currentChatProgress(feature, factory), currentArtifacts: {
            intake: this.latest(featureId, "intake")?.id || null, design: design?.id || null, plan: plan?.id || null, monitor: this.latest(featureId, "monitor")?.id || null,
        } };
    }

    private sharedSummary(featureId: string): Partial<FactoryFeature> {
        const table = this.registry.connection.prepare("SELECT name FROM sqlite_master WHERE name='shared_features'").get();
        if (!table) return {};
        const row = this.registry.connection.prepare("SELECT record_json FROM shared_features WHERE feature_id=?").get(featureId);
        if (!row) return {};
        const record = JSON.parse(String(row.record_json));
        return { shared: { target: record.target, revision: record.revision, digest: record.digest, lock: record.snapshot.lock } };
    }

    outcome(value: unknown): Json { return jsonValue(value); }
}
