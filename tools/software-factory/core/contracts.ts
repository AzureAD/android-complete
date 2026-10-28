// Copyright (c) Microsoft Corporation. All rights reserved.

export const MAX_COMMAND_BYTES = 64 * 1024;

export class FactoryError extends Error {
    readonly code: string;
    readonly status: number;

    constructor(code: string, message: string, status = 400) {
        super(message);
        this.name = "FactoryError";
        this.code = code;
        this.status = status;
    }
}

export interface Requirement {
    id: string;
    text: string;
}

export interface Requirements {
    title: string;
    description?: string;
    designTasks?: string[];
    requirements: Requirement[];
    blockingUnknowns: string[];
}

export interface Revision extends Requirements {
    id: string;
    number: number;
    hash: string;
    createdAt: string;
}

export interface Actor {
    channel: "web" | "chat";
    reference: string;
    sessionId?: string;
}

export interface Approval {
    id: string;
    revisionId: string;
    action: "run_design";
    createdAt: string;
    actor: Actor;
}

export interface AuditEvent {
    sequence: number;
    kind: string;
    revisionId: string;
    createdAt: string;
    actor: Actor;
}

export interface Feature {
    id: string;
    projectId: string;
    status: "requirements_review" | "ready_for_design";
    createdAt: string;
    updatedAt: string;
    current: Revision;
    approvals: Approval[];
    history: Revision[];
    audit: AuditEvent[];
}

export interface Project {
    id: string;
    workspace: string;
}

export type Command =
    | { kind: "create_feature"; requestId: string; input: Requirements }
    | { kind: "revise_requirements"; featureId: string; expectedRevision: string; input: Requirements }
    | { kind: "approve_requirements"; featureId: string; expectedRevision: string };

export interface Consent {
    granted: true;
    revisionId: string;
    actor: Actor;
}

export interface DecisionPresentation {
    title: string; subject: string; context: string; approveLabel: string;
    effect: string; boundaries: string; facts: string[]; review: string;
    canRequestChanges?: boolean;
}

export function object(value: unknown, name: string): Record<string, unknown> {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new FactoryError("invalid_input", `${name} must be an object.`);
    }
    return value as Record<string, unknown>;
}

export function exactKeys(value: Record<string, unknown>, keys: string[], name: string): void {
    const unexpected = Object.keys(value).filter((key) => !keys.includes(key));
    if (unexpected.length) {
        throw new FactoryError("invalid_input", `${name} contains unsupported fields: ${unexpected.join(", ")}.`);
    }
}

export function text(value: unknown, name: string): string {
    if (typeof value !== "string" || !value.trim()) {
        throw new FactoryError("invalid_input", `${name} must be a nonempty string.`);
    }
    return value.trim();
}

export function parseSuccessCriteria(value: unknown, previous: Requirement[] = []): Requirement[] {
    const entries: { id?: string; text: string }[] = [];
    if (typeof value === "string") {
        for (const line of value.split(/\r?\n/)) {
            if (!line.trim()) continue;
            const bullet = /^\s*(?:[-*\u2022]\s+|\d+[.)]\s+)/.exec(line);
            const content = line.slice(bullet?.[0].length || 0).trim();
            const labelled = /^(R\d+)\s*:\s*(.*)$/i.exec(content) ||
                /^([A-Za-z][A-Za-z0-9_-]*)\s*\|\s*(.*)$/.exec(content);
            if (/^\s/.test(line) && !bullet && !labelled && entries.length) {
                entries[entries.length - 1]!.text += ` ${content}`;
            } else entries.push({ ...(labelled ? { id: labelled[1]! } : {}), text: text(labelled ? labelled[2] : content, "Success criterion") });
        }
    } else if (Array.isArray(value)) {
        for (const raw of value) {
            if (typeof raw === "string") entries.push({ text: text(raw, "Success criterion") });
            else {
                const item = object(raw, "Success criterion");
                exactKeys(item, ["id", "text"], "Success criterion");
                entries.push({ ...(item.id === undefined ? {} : { id: text(item.id, "Criterion ID") }), text: text(item.text, "Success criterion") });
            }
        }
    } else throw new FactoryError("invalid_input", "Success criteria must be plain text or a list of outcomes.");
    const explicit = entries.flatMap((entry) => entry.id ? [entry.id] : []);
    if (new Set(explicit).size !== explicit.length) {
        throw new FactoryError("invalid_input", "Requirement IDs must be unique.");
    }
    const reserved = new Set([...previous.map((entry) => entry.id), ...explicit]);
    const used = new Set(explicit);
    let next = 1;
    return entries.map((entry) => {
        if (entry.id) return { id: entry.id, text: entry.text };
        const matching = previous.find((item) => item.text === entry.text && !used.has(item.id));
        if (matching) { used.add(matching.id); return { id: matching.id, text: entry.text }; }
        while (reserved.has(`R${next}`) || used.has(`R${next}`)) next++;
        const id = `R${next++}`;
        used.add(id);
        return { id, text: entry.text };
    });
}

export function parseRequirements(value: unknown): Requirements {
    const input = object(value, "Requirements");
    exactKeys(input, ["title", "description", "requirements", "blockingUnknowns", "designTasks"], "Requirements");
    const title = text(input.title, "Feature title");
    if (input.description !== undefined && typeof input.description !== "string") throw new FactoryError("invalid_input", "The feature description must be plain text.");
    const description = typeof input.description === "string" ? input.description.trim() : "";
    const requirements = parseSuccessCriteria(input.requirements === undefined ? [] : input.requirements);
    if (!description && !requirements.length) {
        throw new FactoryError("invalid_input", "Describe your feature or add at least one success criterion.");
    }
    if (input.blockingUnknowns !== undefined && !Array.isArray(input.blockingUnknowns)) {
        throw new FactoryError("invalid_input", "blockingUnknowns must be an array.");
    }
    const blockingUnknowns = (input.blockingUnknowns || []).map((item: unknown) => text(item, "Open question"));
    if (input.designTasks !== undefined && !Array.isArray(input.designTasks)) throw new FactoryError("invalid_input", "Design tasks must be a list of engineering obligations.");
    const designTasks = (input.designTasks || []).map((item: unknown) => text(item, "Design task"));
    return { title, ...(description ? { description } : {}), ...(designTasks.length ? { designTasks } : {}), requirements, blockingUnknowns };
}

export function parseCommand(value: unknown): Command {
    const command = object(value, "Command");
    if (Buffer.byteLength(JSON.stringify(command), "utf8") > MAX_COMMAND_BYTES) {
        throw new FactoryError("command_too_large", `Commands may not exceed ${MAX_COMMAND_BYTES} bytes.`, 413);
    }
    switch (command.kind) {
        case "create_feature":
            exactKeys(command, ["kind", "requestId", "input"], "Command");
            return { kind: command.kind, requestId: text(command.requestId, "Request ID"), input: parseRequirements(command.input) };
        case "revise_requirements":
            exactKeys(command, ["kind", "featureId", "expectedRevision", "input"], "Command");
            return {
                kind: command.kind, featureId: text(command.featureId, "Feature ID"),
                expectedRevision: text(command.expectedRevision, "Expected revision"), input: parseRequirements(command.input),
            };
        case "approve_requirements":
            exactKeys(command, ["kind", "featureId", "expectedRevision"], "Command");
            return {
                kind: command.kind, featureId: text(command.featureId, "Feature ID"),
                expectedRevision: text(command.expectedRevision, "Expected revision"),
            };
        default:
            throw new FactoryError("unsupported_command", "The synchronous requirements API supports creation, revision, and approval only. Use the shared command dispatcher for lifecycle actions.");
    }
}

export function validateActor(actor: Actor): Actor {
    if (actor.channel !== "web" && actor.channel !== "chat") {
        throw new FactoryError("invalid_actor", "A trusted UI or chat transport is required.");
    }
    const reference = text(actor.reference, "Interaction reference");
    if (actor.channel === "chat") {
        return { channel: "chat", reference, sessionId: text(actor.sessionId, "Owning chat session") };
    }
    return { channel: "web", reference };
}
