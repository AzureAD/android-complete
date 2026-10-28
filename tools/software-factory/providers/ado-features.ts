// Copyright (c) Microsoft Corporation. All rights reserved.

import { FactoryError, object, text } from "../core/contracts.ts";
import { digest, parseSnapshot, sharedTarget, validateTransition, type AdoIdentity, type SharedProvider, type SharedRecord, type SharedSnapshot, type SharedTarget } from "../core/shared-contracts.ts";
import { systemRunner, type CommandRunner } from "./cli.ts";

const encode = encodeURIComponent;
const prefix = "software-factory-state-v1-";
const maximumBytes = 16 * 1024 * 1024;
const quote = (value: string) => value.replaceAll("'", "''");

export class AdoFeatureProvider implements SharedProvider {
    private readonly runner: CommandRunner;
    private readonly fetcher: typeof fetch;
    private readonly verified = new Map<string, SharedRecord>();
    constructor(runner = systemRunner, fetcher = fetch) { this.runner = runner; this.fetcher = fetcher; }

    private async request(target: SharedTarget, suffix: string, method = "GET", body?: string, contentType = "application/json"): Promise<Response> {
        sharedTarget(target);
        const token = await this.runner.run("az", ["account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798", "--query", "accessToken", "-o", "tsv"]);
        const response = await this.fetcher(`https://dev.azure.com/${encode(target.organization)}/${encode(target.project)}/_apis/${suffix}${suffix.includes("?") ? "&" : "?"}api-version=7.1`, {
            method, body, redirect: "error", signal: AbortSignal.timeout(60_000),
            headers: { Authorization: `Bearer ${token}`, "Content-Type": contentType },
        });
        if (!response.ok) throw new FactoryError(response.status === 409 || response.status === 412 ? "shared_conflict" : "shared_unavailable",
            `ADO returned HTTP ${response.status}. No local approval substitutes for the shared record. Refresh and reconcile before retrying.`, response.status === 403 ? 403 : 409);
        return response;
    }

    private async json(target: SharedTarget, suffix: string, method = "GET", body?: unknown, patch = false): Promise<unknown> {
        return (await this.request(target, suffix, method, body === undefined ? undefined : JSON.stringify(body),
            patch ? "application/json-patch+json" : "application/json")).json();
    }

    async identity(target: SharedTarget): Promise<AdoIdentity> {
        const token = await this.runner.run("az", ["account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798", "--query", "accessToken", "-o", "tsv"]);
        const response = await this.fetcher(`https://dev.azure.com/${encode(sharedTarget(target).organization)}/_apis/connectionData?api-version=7.1-preview.1`, {
            redirect: "error", signal: AbortSignal.timeout(60_000), headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) throw new FactoryError("ado_identity_unavailable", "Cannot verify the signed-in ADO identity. No shared approval was recorded.", 403);
        const identity = object(object(await response.json(), "ADO connection").authenticatedUser, "Authenticated ADO user");
        return { id: text(identity.id, "ADO identity ID"), displayName: text(identity.providerDisplayName || identity.customDisplayName, "ADO display name") };
    }

    private relation(value: unknown) {
        const item = object(value, "ADO work item");
        if (!Number.isSafeInteger(item.rev)) throw new FactoryError("shared_corrupt", "ADO omitted its work item revision.", 409);
        const fields = object(item.fields, "ADO fields");
        if (fields["System.WorkItemType"] !== "Feature") throw new FactoryError("shared_feature_required", "Choose an ADO Feature work item, not an implementation PBI.", 409);
        const relations = Array.isArray(item.relations) ? item.relations.map((entry) => object(entry, "ADO relation")) : [];
        const snapshots = relations.filter((entry) => entry.rel === "AttachedFile" &&
            String(object(entry.attributes, "Attachment attributes").name).startsWith(prefix));
        if (snapshots.length > 1) throw new FactoryError("shared_corrupt", "Multiple current Factory snapshots are attached. Resolve the conflicting pointers explicitly.", 409);
        return { item, fields, relations, relation: snapshots[0] || null };
    }

    async read(target: SharedTarget): Promise<SharedRecord | null> {
        return this.load(target);
    }

    private async load(target: SharedTarget, atRevision?: number): Promise<SharedRecord | null> {
        const key = `${digest(target)}:${atRevision}`;
        if (atRevision && this.verified.has(key)) return structuredClone(this.verified.get(key)!);
        const { item, relation } = this.relation(await this.json(target,
            `wit/workitems/${target.workItemId}${atRevision ? `/revisions/${atRevision}` : ""}?$expand=relations`));
        if (!relation) return null;
        const attributes = object(relation.attributes, "Attachment attributes");
        let metadata: Record<string, unknown>;
        try { metadata = object(JSON.parse(text(attributes.comment, "Snapshot provenance")), "Snapshot provenance"); }
        catch { throw new FactoryError("shared_corrupt", "The shared attachment has no valid Factory provenance.", 409); }
        const revision = Number(metadata.revision);
        if (!Number.isSafeInteger(revision) || revision < 1 || revision > Number(item.rev)) throw new FactoryError("shared_corrupt", "Invalid attachment revision.", 409);
        for (let index = revision + 1; index <= Number(item.rev); index++) {
            const later = index === Number(item.rev) ? { relation } : this.relation(await this.json(target, `wit/workitems/${target.workItemId}/revisions/${index}?$expand=relations`));
            if (!later.relation || later.relation.url !== relation.url || object(later.relation.attributes, "Later attachment").comment !== attributes.comment) {
                throw new FactoryError("shared_history_conflict", "A historical attachment was restored over newer Factory history.", 409);
            }
        }
        const historical = this.relation(await this.json(target, `wit/workitems/${target.workItemId}/revisions/${revision}?$expand=relations`));
        const changedBy = object(historical.fields["System.ChangedBy"], "ADO revision author");
        if (changedBy.id !== metadata.actorId || !historical.relation || historical.relation.url !== relation.url ||
            object(historical.relation.attributes, "Historical attachment").comment !== attributes.comment) {
            throw new FactoryError("shared_provenance_changed", "The current attachment does not match its ADO revision and authenticated author.", 409);
        }
        const url = new URL(text(relation.url, "Attachment URL"));
        const organizationPath = `/${encode(target.organization)}/`;
        if (url.protocol !== "https:" || url.hostname !== "dev.azure.com" || !url.pathname.toLowerCase().startsWith(organizationPath.toLowerCase()) ||
            !/\/_apis\/wit\/attachments\/[0-9a-f-]+$/i.test(url.pathname) || url.username || url.password) {
            throw new FactoryError("shared_attachment_target", "Attachment is outside the configured ADO organization.", 409);
        }
        const attachmentId = url.pathname.split("/").at(-1)!;
        const response = await this.request(target, `wit/attachments/${attachmentId}?download=true`);
        const reader = response.body?.getReader();
        if (!reader) throw new FactoryError("shared_corrupt", "ADO returned an empty attachment.", 409);
        const chunks: Uint8Array[] = [];
        let size = 0;
        while (true) {
            const chunk = await reader.read();
            if (chunk.done) break;
            size += chunk.value.length;
            if (size > maximumBytes) { await reader.cancel(); throw new FactoryError("shared_too_large", "Shared snapshot exceeds 16 MiB.", 413); }
            chunks.push(chunk.value);
        }
        const snapshot = parseSnapshot(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        const contentHash = digest(snapshot);
        if (metadata.digest !== contentHash || attributes.name !== `${prefix}${contentHash}.json`) throw new FactoryError("shared_corrupt", "Shared attachment content failed its hash check.", 409);
        let parent: SharedRecord | null = null;
        if (metadata.parentRevision !== null) {
            if (!Number.isSafeInteger(metadata.parentRevision) || Number(metadata.parentRevision) < 1 || Number(metadata.parentRevision) >= revision) throw new FactoryError("shared_corrupt", "Invalid snapshot ancestry.", 409);
            parent = await this.load(target, Number(metadata.parentRevision));
            if (!parent || parent.digest !== metadata.parentDigest) throw new FactoryError("shared_history_conflict", "Shared snapshot ancestry is missing or changed.", 409);
        } else if (metadata.parentDigest !== null) throw new FactoryError("shared_corrupt", "Unexpected root ancestry.", 409);
        validateTransition(parent?.snapshot || null, snapshot, { id: String(changedBy.id), displayName: String(changedBy.displayName || changedBy.id) });
        const record = { target, revision: Number(item.rev), digest: contentHash, snapshot };
        this.verified.set(`${digest(target)}:${revision}`, { ...record, revision });
        return record;
    }

    async write(target: SharedTarget, expected: SharedRecord | null, snapshot: SharedSnapshot, actor: AdoIdentity): Promise<SharedRecord> {
        parseSnapshot(snapshot);
        validateTransition(expected?.snapshot || null, snapshot, actor);
        const current = this.relation(await this.json(target, `wit/workitems/${target.workItemId}?$expand=relations`));
        if ((expected && current.item.rev !== expected.revision) || (!expected && current.relation)) {
            throw new FactoryError("shared_conflict", "Another user changed this ADO Feature. Refresh and review the current scope.", 409);
        }
        const content = JSON.stringify(snapshot);
        if (Buffer.byteLength(content) > maximumBytes) throw new FactoryError("shared_too_large", "Shared snapshot exceeds 16 MiB.", 413);
        const contentHash = digest(snapshot);
        const filename = `${prefix}${contentHash}.json`;
        const uploaded = object(await (await this.request(target, `wit/attachments?fileName=${encode(filename)}`, "POST", content, "application/octet-stream")).json(), "Uploaded attachment");
        const patch: unknown[] = [{ op: "test", path: "/rev", value: current.item.rev }];
        if (current.relation) patch.push({ op: "remove", path: `/relations/${current.relations.indexOf(current.relation)}` });
        patch.push({ op: "add", path: "/relations/-", value: {
            rel: "AttachedFile", url: text(uploaded.url, "Uploaded attachment URL"),
            attributes: { name: filename, comment: JSON.stringify({ digest: contentHash, revision: Number(current.item.rev) + 1, actorId: actor.id,
                parentRevision: current.relation ? object(JSON.parse(text(object(current.relation.attributes, "Attachment").comment, "Provenance")), "Provenance").revision : null,
                parentDigest: expected?.digest || null }) },
        } });
        const tags = String(current.fields["System.Tags"] || "").split(";").map((tag) => tag.trim()).filter(Boolean);
        if (!tags.includes("software-factory")) tags.push("software-factory");
        patch.push({ op: "add", path: "/fields/System.Tags", value: tags.join("; ") });
        patch.push({ op: "add", path: "/fields/System.History", value: `Software Factory: ${snapshot.lock ? `running ${snapshot.lock.kind}` : "stage record updated"}; snapshot ${contentHash}. Approval details and exact artifacts are in the versioned attachment.` });
        const updated = object(await this.json(target, `wit/workitems/${target.workItemId}`, "PATCH", patch, true), "Updated ADO feature");
        const changedBy = object(object(updated.fields, "Updated ADO fields")["System.ChangedBy"], "ADO writer");
        if (changedBy.id !== actor.id || updated.rev !== Number(current.item.rev) + 1) throw new FactoryError("shared_write_uncertain", "ADO did not attest the expected writer/revision. Refresh before any further action.", 409);
        const saved = await this.read(target);
        if (!saved || saved.digest !== contentHash) throw new FactoryError("shared_write_uncertain", "Shared update could not be read back. No downstream action is authorized.", 409);
        return saved;
    }

    async list(organization: string, project: string): Promise<SharedTarget[]> {
        const target = sharedTarget({ organization, project, workItemId: 1 });
        const result = object(await this.json(target, "wit/wiql", "POST", {
            query: `SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '${quote(project)}' AND [System.WorkItemType] = 'Feature' AND [System.Tags] CONTAINS 'software-factory'`,
        }), "Shared feature query");
        if (!Array.isArray(result.workItems)) throw new FactoryError("shared_unavailable", "ADO omitted shared feature query results.", 502);
        return result.workItems.map((item) => sharedTarget({ organization, project, workItemId: object(item, "ADO feature").id }));
    }
}
