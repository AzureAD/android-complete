// Copyright (c) Microsoft Corporation. All rights reserved.

import { FactoryError, object } from "./contracts.ts";

export const QA_CONTEXT_KINDS = [
    "requirement",
    "design-section",
    "review-finding",
    "pbi",
    "implementation",
    "pr",
    "followup",
] as const;

export type FeatureQaContextKind = (typeof QA_CONTEXT_KINDS)[number];

export interface FeatureQaContext {
    kind: FeatureQaContextKind;
    title: string;
    subtitle?: string;
    path?: string;
    snippet?: string;
}

export interface FeatureQaContextSummary {
    requirementsCount: number;
    hasDesign: boolean;
    designReviewRounds: number;
    plannedPbiCount: number;
    implementedPbiCount: number;
    prCount: number;
    readableRepos: string[];
}

export interface FeatureQaLiveStep {
    toolName: string;
    target: string;
    status: "running" | "completed" | "failed";
    timestamp: string;
}

export interface FeatureQaEntry {
    id: string;
    featureId: string;
    sessionId: string;
    turnIndex: number;
    question: string;
    context: FeatureQaContext | null;
    status: "running" | "completed" | "error";
    answer: string;
    error: string | null;
    livePhase: string | null;
    liveSteps: FeatureQaLiveStep[];
    durationMs: number | null;
    createdAt: string;
    completedAt: string | null;
}

export interface FeatureQaSessionInfo {
    sessionId: string;
    turnCount: number;
    contextFingerprint: string;
    staleContext: boolean;
    createdAt: string;
    updatedAt: string;
}

export interface FeatureQaResponse {
    featureId: string;
    activeSession: FeatureQaSessionInfo | null;
    contextSummary: FeatureQaContextSummary;
    entries: FeatureQaEntry[];
}

export type ParsedQaRequest =
    | { action: "ask"; question: string; context: FeatureQaContext | null; resetSession: boolean }
    | { action: "reset" };

function optionalBoundedText(value: unknown, label: string, maxLength: number): string | undefined {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== "string") throw new FactoryError("invalid_input", `${label} must be a string.`, 400);
    const trimmed = value.trim();
    if (!trimmed) return undefined;
    return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength)}\n…[truncated]` : trimmed;
}

export function parseQaContext(input: unknown): FeatureQaContext | null {
    if (input === undefined || input === null) return null;
    const raw = object(input, "Q&A context");
    const kind = raw.kind;
    if (typeof kind !== "string" || !(QA_CONTEXT_KINDS as readonly string[]).includes(kind)) {
        throw new FactoryError("invalid_input", `Q&A context kind must be one of: ${QA_CONTEXT_KINDS.join(", ")}.`, 400);
    }
    const title = optionalBoundedText(raw.title, "Q&A context title", 280);
    if (!title) throw new FactoryError("invalid_input", "Q&A context title cannot be empty.", 400);
    return {
        kind: kind as FeatureQaContextKind,
        title,
        ...(optionalBoundedText(raw.subtitle, "Q&A context subtitle", 280) ? { subtitle: optionalBoundedText(raw.subtitle, "Q&A context subtitle", 280) } : {}),
        ...(optionalBoundedText(raw.path, "Q&A context path", 520) ? { path: optionalBoundedText(raw.path, "Q&A context path", 520) } : {}),
        ...(optionalBoundedText(raw.snippet, "Q&A context snippet", 16_000) ? { snippet: optionalBoundedText(raw.snippet, "Q&A context snippet", 16_000) } : {}),
    };
}

export function parseQaRequest(input: unknown): ParsedQaRequest {
    const raw = object(input, "Q&A request");
    if (raw.action === "reset") {
        return { action: "reset" };
    }
    if (raw.action !== undefined && raw.action !== "ask") {
        throw new FactoryError("invalid_input", `Unsupported Q&A action: ${String(raw.action)}.`, 400);
    }
    if (typeof raw.question !== "string" || !raw.question.trim()) {
        throw new FactoryError("invalid_input", "Please enter a question to ask Copilot.", 400);
    }
    const question = raw.question.trim();
    if (question.length > 8_000) {
        throw new FactoryError("invalid_input", "Question cannot exceed 8,000 characters.", 400);
    }
    if (raw.resetSession !== undefined && typeof raw.resetSession !== "boolean") {
        throw new FactoryError("invalid_input", "resetSession must be a boolean.", 400);
    }
    return {
        action: "ask",
        question,
        context: parseQaContext(raw.context),
        resetSession: Boolean(raw.resetSession),
    };
}
