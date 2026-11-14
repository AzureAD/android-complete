// Copyright (c) Microsoft Corporation. All rights reserved.

import type { WorkspaceAnalytics } from "../../core/analytics.ts";
import type { FeatureQaContext, FeatureQaLiveStep, FeatureQaResponse } from "../../core/qa-contracts.ts";
import type { FeatureProvenance } from "../../core/registry.ts";
import type { WorktreeInfo } from "../../core/runtime-health.ts";

export type { FeatureProvenance, WorktreeInfo, WorkspaceAnalytics };

export type StudioSseEvent =
    | { type: "connected"; timestamp: string; port: number }
    | { type: "state-changed"; reason: string; featureId?: string; timestamp: string }
    | { type: "qa-started"; featureId: string; entryId: string; sessionId: string; turnIndex: number; livePhase: string }
    | { type: "qa-delta"; featureId: string; entryId: string; delta: string; livePhase: string }
    | { type: "qa-step"; featureId: string; entryId: string; livePhase: string; steps: FeatureQaLiveStep[] }
    | { type: "qa-phase"; featureId: string; entryId: string; livePhase: string }
    | { type: "qa-complete"; featureId: string; entryId: string; status: "completed" | "error" }
    | { type: "qa-reset"; featureId: string };

export type LiveStreamStatus = "connecting" | "live" | "reconnecting" | "polling";

let currentCsrf = "";
let sessionListeners: Array<(csrf: string) => void> = [];
let rehandshakeInFlight: Promise<string> | null = null;

export function setCsrfToken(token: string): void {
    currentCsrf = token;
}

export function getCsrfToken(): string {
    return currentCsrf;
}

export function onSessionRefreshed(listener: (csrf: string) => void): () => void {
    sessionListeners.push(listener);
    return () => {
        sessionListeners = sessionListeners.filter((entry) => entry !== listener);
    };
}

async function refreshSession(): Promise<string> {
    if (rehandshakeInFlight) return rehandshakeInFlight;
    rehandshakeInFlight = (async () => {
        const response = await fetch("/api/session", { credentials: "same-origin" });
        const payload: unknown = await response.json();
        if (!response.ok || !payload || typeof payload !== "object" || !("csrf" in payload) || typeof payload.csrf !== "string") {
            throw new Error(`Session handshake failed (${response.status}).`);
        }
        currentCsrf = payload.csrf;
        for (const listener of sessionListeners) listener(payload.csrf);
        return payload.csrf;
    })().finally(() => {
        rehandshakeInFlight = null;
    });
    return rehandshakeInFlight;
}

function extractErrorMessage(status: number, payload: unknown): string {
    if (payload && typeof payload === "object" && "error" in payload &&
        payload.error && typeof payload.error === "object" && "message" in payload.error &&
        typeof payload.error.message === "string") {
        return payload.error.message;
    }
    return `Request failed (${status}).`;
}

function isSessionExpired(status: number, payload: unknown): boolean {
    if (status !== 401) return false;
    if (payload && typeof payload === "object" && "error" in payload &&
        payload.error && typeof payload.error === "object" && "code" in payload.error) {
        return payload.error.code === "session_required" || payload.error.code === "csrf_required";
    }
    return true;
}

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    const payload: unknown = await response.json();
    if (!response.ok) {
        const method = (options?.method || "GET").toUpperCase();
        const canSafeRetry = url !== "/api/session" && (method === "GET" || url === "/api/commands/preview");
        if (isSessionExpired(response.status, payload) && url !== "/api/session") {
            try {
                const freshCsrf = await refreshSession();
                if (canSafeRetry) {
                    const nextHeaders = new Headers(options?.headers);
                    if (nextHeaders.has("X-Software-Factory-CSRF")) {
                        nextHeaders.set("X-Software-Factory-CSRF", freshCsrf);
                    }
                    const retried = await fetch(url, { credentials: "same-origin", ...options, headers: nextHeaders });
                    const retriedPayload: unknown = await retried.json();
                    if (!retried.ok) throw new Error(extractErrorMessage(retried.status, retriedPayload));
                    return retriedPayload as T;
                }
            } catch {
                // Fall through and surface original error if server is unreachable.
            }
        }
        throw new Error(extractErrorMessage(response.status, payload));
    }
    return payload as T;
}

export async function fetchFeatureQa(featureId: string): Promise<FeatureQaResponse> {
    return request<FeatureQaResponse>(`/api/features/${encodeURIComponent(featureId)}/qa`);
}

export async function askFeatureQa(
    featureId: string,
    question: string,
    context?: FeatureQaContext | null,
    resetSession?: boolean,
): Promise<FeatureQaResponse> {
    const csrf = currentCsrf || await refreshSession();
    return request<FeatureQaResponse>(`/api/features/${encodeURIComponent(featureId)}/qa`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Software-Factory-CSRF": csrf,
        },
        body: JSON.stringify({
            action: "ask",
            question,
            ...(context ? { context } : {}),
            ...(resetSession ? { resetSession: true } : {}),
        }),
    });
}

export async function resetFeatureQa(featureId: string): Promise<FeatureQaResponse> {
    const csrf = currentCsrf || await refreshSession();
    return request<FeatureQaResponse>(`/api/features/${encodeURIComponent(featureId)}/qa`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Software-Factory-CSRF": csrf,
        },
        body: JSON.stringify({ action: "reset" }),
    });
}

export async function fetchWorkspaceAnalytics(publishedOnly = false): Promise<{
    worktree: WorktreeInfo;
    analytics: WorkspaceAnalytics;
}> {
    return request(`/api/analytics${publishedOnly ? "?published=1" : ""}`);
}

type SseSubscriber = {
    onEvent: (event: StudioSseEvent) => void;
    onStatus?: (status: LiveStreamStatus) => void;
};

let sharedSource: EventSource | null = null;
let sharedStatus: LiveStreamStatus = "connecting";
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const sseSubscribers = new Set<SseSubscriber>();

function updateSseStatus(next: LiveStreamStatus): void {
    sharedStatus = next;
    for (const sub of sseSubscribers) {
        sub.onStatus?.(next);
    }
}

function dispatchSse(event: StudioSseEvent): void {
    for (const sub of sseSubscribers) {
        try { sub.onEvent(event); } catch { /* ignore subscriber error */ }
    }
}

function ensureSharedEventSource(): void {
    if (sseSubscribers.size === 0) return;
    if (typeof EventSource === "undefined") {
        updateSseStatus("polling");
        return;
    }
    if (sharedSource) return;

    const source = new EventSource("/api/events", { withCredentials: true });
    sharedSource = source;

    const bindNamedEvent = <T extends StudioSseEvent["type"]>(eventType: T) => {
        source.addEventListener(eventType, (raw: MessageEvent) => {
            try {
                const parsed = JSON.parse(String(raw.data)) as Record<string, unknown>;
                dispatchSse({ ...parsed, type: eventType } as StudioSseEvent);
            } catch {
                // Ignore malformed event frames.
            }
        });
    };

    bindNamedEvent("connected");
    bindNamedEvent("state-changed");
    bindNamedEvent("qa-started");
    bindNamedEvent("qa-delta");
    bindNamedEvent("qa-step");
    bindNamedEvent("qa-phase");
    bindNamedEvent("qa-complete");
    bindNamedEvent("qa-reset");

    source.onopen = () => {
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        updateSseStatus("live");
    };

    source.onerror = () => {
        updateSseStatus("reconnecting");
        source.close();
        if (sharedSource === source) {
            sharedSource = null;
        }
        if (sseSubscribers.size > 0 && !reconnectTimer) {
            reconnectTimer = setTimeout(() => {
                reconnectTimer = null;
                void refreshSession()
                    .catch(() => { /* retry EventSource anyway */ })
                    .finally(() => {
                        if (sseSubscribers.size > 0) ensureSharedEventSource();
                    });
            }, 2000);
        }
    };
}

export function subscribeStudioEvents(subscriber: SseSubscriber): () => void {
    sseSubscribers.add(subscriber);
    ensureSharedEventSource();
    subscriber.onStatus?.(sharedStatus);
    return () => {
        sseSubscribers.delete(subscriber);
        if (sseSubscribers.size === 0) {
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            if (sharedSource) {
                sharedSource.close();
                sharedSource = null;
            }
            sharedStatus = "connecting";
        }
    };
}