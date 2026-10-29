// Copyright (c) Microsoft Corporation. All rights reserved.

import { createHash } from "node:crypto";
import { FactoryError, object, parseRequirements, text, validateActor, type Feature } from "./contracts.ts";
import { jsonValue, parseConfiguration, sharedConfiguration, type ConfigRevision, type FactoryState } from "./lifecycle-contracts.ts";

export interface SharedTarget { organization: string; project: string; workItemId: number }
export type PublishTarget = SharedTarget | { organization: string; project: string; create: true; areaPath?: string; iterationPath?: string };
export interface AdoIdentity { id: string; displayName: string }
export interface SharedSnapshot {
    version: 1;
    feature: Feature;
    state: FactoryState;
    configuration: ConfigRevision;
    lock: { id: string; kind: string; actor: AdoIdentity; createdAt: string } | null;
}
export interface SharedRecord {
    target: SharedTarget;
    revision: number;
    digest: string;
    snapshot: SharedSnapshot;
}
export interface SharedProvider {
    identity(target: SharedTarget): Promise<AdoIdentity>;
    read(target: SharedTarget): Promise<SharedRecord | null>;
    write(target: SharedTarget, expected: SharedRecord | null, snapshot: SharedSnapshot, actor: AdoIdentity): Promise<SharedRecord>;
    list(organization: string, project: string): Promise<SharedTarget[]>;
    createFeature?(target: { organization: string; project: string; areaPath?: string; iterationPath?: string }, title: string, description?: string): Promise<SharedTarget>;
}

export const digest = (value: unknown) => createHash("sha256").update(JSON.stringify(value)).digest("hex");

export function sharedTarget(value: unknown): SharedTarget {
    const raw = object(value, "Shared ADO target");
    const organization = text(raw.organization, "ADO organization");
    if (!/^[a-zA-Z0-9_-]+$/.test(organization)) throw new FactoryError("invalid_shared_target", "Use an ADO organization name, not a URL.");
    if (!Number.isSafeInteger(raw.workItemId) || Number(raw.workItemId) < 1) throw new FactoryError("invalid_shared_target", "Choose an existing ADO Feature work item.");
    return { organization, project: text(raw.project, "ADO project"), workItemId: Number(raw.workItemId) };
}

export function publishTarget(value: unknown, boards?: { organization: string; project: string; areaPath?: string; iterationPath?: string }): PublishTarget {
    const raw = object(value, "Shared ADO target");
    if (raw.create === true) {
        if (raw.workItemId !== undefined) throw new FactoryError("invalid_shared_target", "Do not specify both create: true and an existing workItemId.");
        const organization = text(raw.organization ?? boards?.organization, "ADO organization");
        if (!/^[a-zA-Z0-9_-]+$/.test(organization)) throw new FactoryError("invalid_shared_target", "Use an ADO organization name, not a URL.");
        const project = text(raw.project ?? boards?.project, "ADO project");
        const areaPath = typeof raw.areaPath === "string" && raw.areaPath.trim() ? raw.areaPath.trim() : boards?.areaPath || undefined;
        const iterationPath = typeof raw.iterationPath === "string" && raw.iterationPath.trim() ? raw.iterationPath.trim() : boards?.iterationPath || undefined;
        return { organization, project, create: true, ...(areaPath ? { areaPath } : {}), ...(iterationPath ? { iterationPath } : {}) };
    }
    return sharedTarget( {
        organization: raw.organization ?? boards?.organization,
        project: raw.project ?? boards?.project,
        workItemId: raw.workItemId,
    });
}

export function parseSnapshot(value: unknown): SharedSnapshot {
    const raw = object(value, "Shared feature snapshot");
    if (raw.version !== 1) throw new FactoryError("shared_version", "This ADO feature uses an unsupported Factory format.", 409);
    const feature = object(raw.feature, "Shared feature");
    text(feature.id, "Feature ID");
    const current = object(feature.current, "Current requirements");
    if (!Array.isArray(feature.history) || !Array.isArray(feature.approvals) || !Array.isArray(feature.audit)) {
        throw new FactoryError("shared_corrupt", "The ADO feature is missing version history.", 409);
    }
    const revisionIds = new Set<string>();
    for (const [index, entry] of feature.history.entries()) {
        const revision = object(entry, "Requirements revision");
        const input = parseRequirements({ title: revision.title, description: revision.description,
            designTasks: revision.designTasks, requirements: revision.requirements, blockingUnknowns: revision.blockingUnknowns });
        if (revision.hash !== digest(input)) throw new FactoryError("shared_corrupt", "Requirements content does not match its stored hash.", 409);
        const id = text(revision.id, "Revision ID");
        if (revisionIds.has(id) || revision.number !== index + 1) throw new FactoryError("shared_corrupt", "Requirements revisions must be unique and sequential.", 409);
        revisionIds.add(id);
        text(revision.createdAt, "Revision time");
    }
    if (digest(feature.history.at(-1)) !== digest(current)) throw new FactoryError("shared_corrupt", "Current requirements must be the latest immutable revision.", 409);
    text(feature.createdAt, "Feature creation time"); text(feature.updatedAt, "Feature update time");
    const approvals = new Set<string>();
    for (const rawApproval of feature.approvals) {
        const approval = object(rawApproval, "Requirements approval");
        text(approval.id, "Approval ID"); text(approval.createdAt, "Approval time");
        const revisionId = text(approval.revisionId, "Approved revision");
        if (!revisionIds.has(revisionId) || approvals.has(revisionId) || approval.action !== "run_design") throw new FactoryError("shared_corrupt", "Invalid requirements approval lineage.", 409);
        approvals.add(revisionId);
        sharedActor(approval.actor);
    }
    if (feature.status !== (approvals.has(String(current.id)) ? "ready_for_design" : "requirements_review")) throw new FactoryError("shared_corrupt", "Feature status disagrees with approval history.", 409);
    for (const [index, rawEvent] of feature.audit.entries()) {
        const event = object(rawEvent, "Requirements audit");
        if (event.sequence !== index + 1 || !revisionIds.has(String(event.revisionId))) throw new FactoryError("shared_corrupt", "Invalid audit lineage.", 409);
        text(event.kind, "Audit kind"); text(event.createdAt, "Audit time"); sharedActor(event.actor);
    }
    const state = object(raw.state, "Shared lifecycle");
    if (state.version !== 2 || typeof state.cancelled !== "boolean") throw new FactoryError("shared_corrupt", "Invalid shared lifecycle version/state.", 409);
    for (const key of ["artifacts", "decisions", "feedback", "operations", "executions", "events", "progress", "controls", "prBindings", "waivers"]) {
        if (!Array.isArray(state[key])) throw new FactoryError("shared_corrupt", `Missing shared ${key}.`, 409);
    }
    const artifactIds = new Set<string>();
    for (const item of state.artifacts as unknown[]) {
        const artifact = object(item, "Shared artifact");
        if (artifact.hash !== digest(artifact.content)) throw new FactoryError("shared_corrupt", "An artifact hash does not match its content.", 409);
        const id = text(artifact.id, "Artifact ID");
        if (artifactIds.has(id) || !revisionIds.has(String(artifact.requirementsRevision)) ||
            (artifact.parentId !== null && !artifactIds.has(String(artifact.parentId))) ||
            !["intake", "design", "plan", "implementation", "implementation_attempt", "monitor", "legacy", "planning_attempt"].includes(String(artifact.kind)) ||
            !Number.isSafeInteger(artifact.generation) || Number(artifact.generation) < 0) throw new FactoryError("shared_corrupt", "Invalid artifact lineage.", 409);
        artifactIds.add(id);
        text(artifact.createdAt, "Artifact time");
    }
    const decisionIds = new Set<string>();
    for (const item of state.decisions as unknown[]) {
        const decision = object(item, "Shared decision");
        if (decision.scopeHash !== digest(decision.scope)) throw new FactoryError("shared_corrupt", "An approval scope hash is invalid.", 409);
        const id = text(decision.id, "Decision ID");
        if (decisionIds.has(id) || typeof decision.approved !== "boolean" ||
            !["design", "plan", "backlog", "dispatch", "complete"].includes(String(decision.stage))) throw new FactoryError("shared_corrupt", "Invalid decision.", 409);
        decisionIds.add(id);
        text(decision.targetId, "Decision target"); text(decision.createdAt, "Decision time"); sharedActor(decision.actor);
    }
    if ((state.executions as unknown[]).length || (state.controls as unknown[]).length ||
        (state.progress as unknown[]).length || state.sessionBinding !== null || state.schedule !== null) throw new FactoryError("shared_corrupt", "Native sessions and schedules must remain local.", 409);
    if (state.completed !== null) {
        const completed = object(state.completed, "Completion");
        if (!revisionIds.has(String(completed.revision)) || !artifactIds.has(String(completed.monitorId)) || !decisionIds.has(String(completed.decisionId))) throw new FactoryError("shared_corrupt", "Completion has missing evidence.", 409);
    }
    const operationKeys = new Set<string>();
    for (const entry of state.operations as unknown[]) {
        const operation = object(entry, "Provider operation");
        const key = text(operation.key, "Operation key");
        if (operationKeys.has(key) || !["inflight", "completed", "reconciliation_required", "failed"].includes(String(operation.status)) ||
            !["work_item", "links", "dispatch", "dispatch_status", "feedback"].includes(String(operation.kind)) ||
            !artifactIds.has(String(operation.planId))) throw new FactoryError("shared_corrupt", "Invalid provider operation.", 409);
        operationKeys.add(key);
        for (const field of ["claimId", "configurationId", "itemId", "approvalId", "updatedAt"]) text(operation[field], field);
    }
    const configuration = object(raw.configuration, "Shared configuration");
    const portable = sharedConfiguration(parseConfiguration(configuration.value));
    if (configuration.hash !== digest(portable) || digest(configuration.value) !== digest(portable)) throw new FactoryError("shared_corrupt", "Invalid portable configuration hash.", 409);
    text(configuration.id, "Configuration ID");
    if (raw.lock !== null) {
        const lock = object(raw.lock, "Shared execution claim");
        text(lock.id, "Claim ID"); text(lock.kind, "Claim kind"); text(lock.createdAt, "Claim time");
        text(object(lock.actor, "Claim actor").id, "ADO identity");
        text(object(lock.actor, "Claim actor").displayName, "ADO display name");
    }
    // Validate serializability before accepting data from the external system.
    jsonValue(raw);
    return value as SharedSnapshot;
}

function sharedActor(value: unknown) {
    const raw = object(value, "Shared actor");
    if (raw.channel !== "web" && raw.channel !== "chat") throw new FactoryError("shared_corrupt", "Invalid shared actor channel.", 409);
    const ado = object(raw.ado, "ADO actor");
    const actor = validateActor({ channel: raw.channel, reference: text(raw.reference, "Actor reference"),
        ...(raw.sessionId === undefined ? {} : { sessionId: text(raw.sessionId, "Actor session") }),
        ado: { id: text(ado.id, "ADO identity"), displayName: text(ado.displayName, "ADO display name") } });
    if (!actor.ado) throw new FactoryError("shared_corrupt", "Shared approval/audit history requires an authenticated ADO actor.", 409);
    return actor;
}

export function validateTransition(previous: SharedSnapshot | null, next: SharedSnapshot, writer: AdoIdentity): void {
    const unchangedPrefix = (before: unknown[], after: unknown[]) => {
        if (before.length > after.length || before.some((entry, index) => digest(entry) !== digest(after[index]))) {
            throw new FactoryError("shared_history_conflict", "Shared immutable history was removed or rewritten.", 409);
        }
    };
    if (previous) {
        if (previous.feature.id !== next.feature.id || previous.feature.createdAt !== next.feature.createdAt) throw new FactoryError("shared_history_conflict", "Shared feature identity changed.", 409);
        for (const key of ["history", "approvals", "audit"] as const) unchangedPrefix(previous.feature[key], next.feature[key]);
        for (const key of ["artifacts", "decisions", "feedback", "waivers"] as const) unchangedPrefix(previous.state[key], next.state[key]);
    } else if (next.feature.history.length !== 1 || next.feature.approvals.length || next.state.artifacts.length || next.state.decisions.length || next.lock) {
        throw new FactoryError("shared_history_conflict", "Publish a clean successor; historical approvals cannot establish shared authority.", 409);
    }
    for (const entry of [
        ...next.feature.approvals.slice(previous?.feature.approvals.length || 0),
        ...next.feature.audit.slice(previous?.feature.audit.length || 0),
        ...next.state.decisions.slice(previous?.state.decisions.length || 0),
    ]) {
        if (sharedActor(entry.actor).ado!.id !== writer.id) throw new FactoryError("shared_provenance_changed", "New approvals/audit events do not match the authenticated ADO revision author.", 409);
    }
}
