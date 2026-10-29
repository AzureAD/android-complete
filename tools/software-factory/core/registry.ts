// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, realpathSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { SQLOutputValue } from "node:sqlite";
import {
    FactoryError, parseRequirements, validateActor,
    type Actor, type Approval, type AuditEvent, type Feature, type Project, type Requirements, type Revision,
} from "./contracts.ts";
import { lifecycleSchema } from "./lifecycle-store.ts";
import { resolveRepositoryRoot } from "./runtime-health.ts";

type Row = Record<string, SQLOutputValue>;

function column(row: Row, key: string): string {
    const value = row[key];
    if (typeof value !== "string") throw new FactoryError("storage_corrupt", `Invalid stored ${key}.`, 500);
    return value;
}

function actorFromJson(json: string): Actor {
    const value: unknown = JSON.parse(json);
    if (value === null || typeof value !== "object" || !("channel" in value) || !("reference" in value) ||
        (value.channel !== "web" && value.channel !== "chat") || typeof value.reference !== "string") {
        throw new FactoryError("storage_corrupt", "Invalid stored actor.", 500);
    }
    const sessionId = "sessionId" in value && typeof value.sessionId === "string" ? value.sessionId : undefined;
    const ado = "ado" in value && value.ado && typeof value.ado === "object" && "id" in value.ado && "displayName" in value.ado &&
        typeof value.ado.id === "string" && typeof value.ado.displayName === "string" ? { id: value.ado.id, displayName: value.ado.displayName } : undefined;
    return validateActor({ channel: value.channel, reference: value.reference, sessionId, ...(ado ? { ado } : {}) });
}

export function contentHash(input: Requirements): string {
    return createHash("sha256").update(JSON.stringify(input)).digest("hex");
}

export class Registry {
    private readonly db: DatabaseSync;
    readonly project: Project;
    readonly databasePath: string;

    constructor(databasePath: string, workspace: string) {
        const canonical = realpathSync(workspace);
        const repoRoot = resolveRepositoryRoot(canonical);
        const identity = process.platform === "win32" ? repoRoot.toLowerCase() : repoRoot;
        mkdirSync(path.dirname(databasePath), { recursive: true, mode: 0o700 });
        const directory = realpathSync(path.dirname(path.resolve(databasePath)));
        const relative = path.relative(canonical, directory);
        if (relative === "" || (relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative))) {
            throw new FactoryError("storage_in_workspace", "Store Software Factory data outside the checkout. Set SOFTWARE_FACTORY_HOME to a private per-user directory.", 409);
        }
        this.databasePath = path.join(directory, path.basename(databasePath));
        this.db = new DatabaseSync(this.databasePath);
        try {
            this.db.exec("PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000; PRAGMA journal_mode = WAL;");
            const version = this.db.prepare("PRAGMA user_version").get()?.user_version;
            if (version !== 0 && version !== 1 && version !== 2) {
                throw new FactoryError("storage_version", "This registry was created by an unsupported schema version. Do not overwrite it.", 500);
            }
            if (version === 1) {
                this.db.prepare("VACUUM INTO ?").run(path.join(directory, `registry-before-v2-${randomUUID()}.sqlite`));
            }
            this.transaction(() => {
                this.db.exec(`
                    CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, workspace TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS features (
                        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                        request_id TEXT NOT NULL, request_hash TEXT NOT NULL, current_revision TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id, request_id)
                    );
                    CREATE TABLE IF NOT EXISTS revisions (
                        id TEXT PRIMARY KEY, feature_id TEXT NOT NULL REFERENCES features(id),
                        number INTEGER NOT NULL, hash TEXT NOT NULL, input_json TEXT NOT NULL, created_at TEXT NOT NULL,
                        UNIQUE(feature_id, number)
                    );
                    CREATE TABLE IF NOT EXISTS approvals (
                        id TEXT PRIMARY KEY, feature_id TEXT NOT NULL REFERENCES features(id),
                        revision_id TEXT NOT NULL REFERENCES revisions(id), actor_json TEXT NOT NULL, created_at TEXT NOT NULL,
                        UNIQUE(feature_id, revision_id)
                    );
                    CREATE TABLE IF NOT EXISTS audit (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT, feature_id TEXT NOT NULL REFERENCES features(id),
                        kind TEXT NOT NULL, revision_id TEXT NOT NULL REFERENCES revisions(id),
                        actor_json TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS feature_project ON features(project_id, updated_at);
                    PRAGMA user_version = 2;
                `);
                lifecycleSchema(this.db);
                for (const table of ["revisions", "approvals", "audit"]) {
                    for (const operation of ["UPDATE", "DELETE"]) {
                        this.db.exec(`CREATE TRIGGER IF NOT EXISTS ${table}_${operation.toLowerCase()}_immutable
                            BEFORE ${operation} ON ${table} BEGIN SELECT RAISE(ABORT, 'Immutable history'); END;`);
                    }
                }
            });
            this.db.prepare("INSERT INTO projects (id, identity, workspace) VALUES (?, ?, ?) ON CONFLICT(identity) DO NOTHING")
                .run(randomUUID(), identity, repoRoot);
            const row = this.db.prepare("SELECT id, workspace FROM projects WHERE identity = ?").get(identity);
            if (!row) throw new FactoryError("storage_corrupt", "Project registration failed.", 500);
            this.project = { id: column(row, "id"), workspace: canonical };
        } catch (error) {
            this.db.close();
            throw error;
        }
    }

    close(): void {
        this.db.close();
    }

    get connection(): DatabaseSync { return this.db; }

    private transaction<T>(action: () => T): T {
        this.db.exec("BEGIN IMMEDIATE");
        try {
            const result = action();
            this.db.exec("COMMIT");
            return result;
        } catch (error) {
            this.db.exec("ROLLBACK");
            throw error;
        }
    }

    private revision(row: Row): Revision {
        const input: unknown = JSON.parse(column(row, "input_json"));
        const number = row.number;
        if (typeof number !== "number") throw new FactoryError("storage_corrupt", "Invalid revision number.", 500);
        return {
            ...parseRequirements(input), id: column(row, "id"), number,
            hash: column(row, "hash"), createdAt: column(row, "created_at"),
        };
    }

    get(featureId: string): Feature {
        const row = this.db.prepare("SELECT * FROM features WHERE id = ? AND project_id = ?").get(featureId, this.project.id);
        if (!row) throw new FactoryError("feature_not_found", "Feature not found in this project.", 404);
        const history = this.db.prepare("SELECT * FROM revisions WHERE feature_id = ? ORDER BY number").all(featureId).map((item) => this.revision(item));
        const current = history.find((item) => item.id === column(row, "current_revision"));
        if (!current) throw new FactoryError("storage_corrupt", "Current requirements revision is missing.", 500);
        const approvals: Approval[] = this.db.prepare("SELECT * FROM approvals WHERE feature_id = ? ORDER BY created_at").all(featureId).map((item) => ({
            id: column(item, "id"), revisionId: column(item, "revision_id"), action: "run_design",
            createdAt: column(item, "created_at"), actor: actorFromJson(column(item, "actor_json")),
        }));
        const audit: AuditEvent[] = this.db.prepare("SELECT * FROM audit WHERE feature_id = ? ORDER BY sequence").all(featureId).map((item) => {
            if (typeof item.sequence !== "number") throw new FactoryError("storage_corrupt", "Invalid audit sequence.", 500);
            return {
                sequence: item.sequence, kind: column(item, "kind"), revisionId: column(item, "revision_id"),
                createdAt: column(item, "created_at"), actor: actorFromJson(column(item, "actor_json")),
            };
        });
        return {
            id: featureId, projectId: this.project.id,
            status: approvals.some((item) => item.revisionId === current.id) ? "ready_for_design" : "requirements_review",
            createdAt: column(row, "created_at"), updatedAt: column(row, "updated_at"), current, history, approvals, audit,
        };
    }

    list(): Feature[] {
        return this.db.prepare("SELECT id FROM features WHERE project_id = ? ORDER BY updated_at DESC, id")
            .all(this.project.id).map((row) => this.get(column(row, "id")));
    }

    importShared(feature: Feature): void {
        this.transaction(() => {
            const existing = this.db.prepare("SELECT project_id FROM features WHERE id=?").get(feature.id);
            if (existing && existing.project_id !== this.project.id) throw new FactoryError("shared_project_conflict", "This shared feature is already cached for another workspace in this registry.", 409);
            if (existing) {
                const prior = this.get(feature.id);
                for (const key of ["history", "approvals", "audit"] as const) {
                    const before = key === "audit" ? prior.audit.map((entry, index) => ({ ...entry, sequence: index + 1 })) : prior[key];
                    if (before.length > feature[key].length || before.some((entry, index) => JSON.stringify(entry) !== JSON.stringify(feature[key][index]))) {
                        throw new FactoryError("shared_history_conflict", "Shared history diverges from the previously verified cache.", 409);
                    }
                }
            }
            this.db.prepare("INSERT OR IGNORE INTO features VALUES (?, ?, ?, ?, ?, ?, ?)")
                .run(feature.id, this.project.id, `ado:${feature.id}`, feature.history[0]!.hash, feature.current.id, feature.createdAt, feature.updatedAt);
            for (const revision of feature.history) {
                const prior = this.db.prepare("SELECT hash FROM revisions WHERE id=? AND feature_id=?").get(revision.id, feature.id);
                if (prior && prior.hash !== revision.hash) throw new FactoryError("shared_history_conflict", "Shared requirements conflict with immutable local history.", 409);
                if (!prior) {
                    const input: Requirements = { title: revision.title, ...(revision.description ? { description: revision.description } : {}),
                        ...(revision.designTasks?.length ? { designTasks: revision.designTasks } : {}), requirements: revision.requirements, blockingUnknowns: revision.blockingUnknowns };
                    this.addRevision(feature.id, revision.id, revision.number, input, revision.createdAt);
                }
            }
            for (const approval of feature.approvals) {
                const prior = this.db.prepare("SELECT id FROM approvals WHERE feature_id=? AND revision_id=?").get(feature.id, approval.revisionId);
                if (prior && prior.id !== approval.id) throw new FactoryError("shared_history_conflict", "Shared approval conflicts with local history.", 409);
                if (!prior) this.db.prepare("INSERT INTO approvals VALUES (?, ?, ?, ?, ?)")
                    .run(approval.id, feature.id, approval.revisionId, JSON.stringify(validateActor(approval.actor)), approval.createdAt);
            }
            const audit = this.db.prepare("SELECT sequence FROM audit WHERE feature_id=? ORDER BY sequence").all(feature.id);
            for (const entry of feature.audit.slice(audit.length)) this.event(feature.id, entry.kind, entry.revisionId, validateActor(entry.actor), entry.createdAt);
            this.db.prepare("UPDATE features SET current_revision=?, updated_at=? WHERE id=?").run(feature.current.id, feature.updatedAt, feature.id);
        });
    }

    private addRevision(featureId: string, id: string, number: number, input: Requirements, now: string): void {
        this.db.prepare("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)")
            .run(id, featureId, number, contentHash(input), JSON.stringify(input), now);
    }

    private event(featureId: string, kind: string, revisionId: string, actor: Actor, now: string): void {
        this.db.prepare("INSERT INTO audit (feature_id, kind, revision_id, actor_json, created_at) VALUES (?, ?, ?, ?, ?)")
            .run(featureId, kind, revisionId, JSON.stringify(actor), now);
    }

    create(requestId: string, input: Requirements, actor: Actor): Feature {
        return this.transaction(() => {
            const existing = this.db.prepare("SELECT id, request_hash FROM features WHERE project_id = ? AND request_id = ?").get(this.project.id, requestId);
            if (existing) {
                if (column(existing, "request_hash") !== contentHash(input)) {
                    throw new FactoryError("request_conflict", "This request ID was already used with different requirements.", 409);
                }
                return this.get(column(existing, "id"));
            }
            const id = randomUUID();
            const revision = randomUUID();
            const now = new Date().toISOString();
            this.db.prepare("INSERT INTO features VALUES (?, ?, ?, ?, ?, ?, ?)")
                .run(id, this.project.id, requestId, contentHash(input), revision, now, now);
            this.addRevision(id, revision, 1, input, now);
            this.event(id, "feature_created", revision, actor, now);
            return this.get(id);
        });
    }

    revise(featureId: string, expectedRevision: string, input: Requirements, actor: Actor): Feature {
        return this.transaction(() => {
            const feature = this.get(featureId);
            this.requireRevision(feature, expectedRevision);
            if (feature.current.hash === contentHash(input)) return feature;
            const revision = randomUUID();
            const now = new Date().toISOString();
            this.addRevision(featureId, revision, feature.current.number + 1, input, now);
            this.db.prepare("UPDATE features SET current_revision = ?, updated_at = ? WHERE id = ?").run(revision, now, featureId);
            this.event(featureId, "requirements_revised", revision, actor, now);
            return this.get(featureId);
        });
    }

    approve(featureId: string, expectedRevision: string, actor: Actor): Feature {
        return this.transaction(() => {
            const feature = this.get(featureId);
            this.requireRevision(feature, expectedRevision);
            if (!feature.current.requirements.length) {
                throw new FactoryError("success_criteria_required", "Add and review at least one success criterion before approving this feature for design.", 409);
            }
            if (feature.current.blockingUnknowns.length) {
                throw new FactoryError("blocking_unknowns", "Resolve the open questions before approving this feature for design.", 409);
            }
            if (feature.status === "ready_for_design") return feature;
            const now = new Date().toISOString();
            this.db.prepare("INSERT INTO approvals VALUES (?, ?, ?, ?, ?)")
                .run(randomUUID(), featureId, expectedRevision, JSON.stringify(actor), now);
            this.db.prepare("UPDATE features SET updated_at = ? WHERE id = ?").run(now, featureId);
            this.event(featureId, "requirements_approved", expectedRevision, actor, now);
            return this.get(featureId);
        });
    }

    private requireRevision(feature: Feature, expected: string): void {
        if (feature.current.id !== expected) {
            throw new FactoryError("stale_revision", "Requirements changed. Read the current revision and review it again.", 409);
        }
    }
}
