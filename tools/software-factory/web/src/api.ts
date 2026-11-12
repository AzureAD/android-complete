// Copyright (c) Microsoft Corporation. All rights reserved.

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    const payload: unknown = await response.json();
    if (!response.ok) {
        let message = `Request failed (${response.status}).`;
        if (payload && typeof payload === "object" && "error" in payload &&
            payload.error && typeof payload.error === "object" && "message" in payload.error &&
            typeof payload.error.message === "string") message = payload.error.message;
        throw new Error(message);
    }
    return payload as T;
}
