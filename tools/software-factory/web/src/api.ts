// Copyright (c) Microsoft Corporation. All rights reserved.

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
