// Copyright (c) Microsoft Corporation. All rights reserved.

import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import http from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Application } from "../core/application.ts";
import { FactoryError, MAX_COMMAND_BYTES, type Actor } from "../core/contracts.ts";
import { runtimeReport } from "../core/doctor.ts";

function matches(actual: string | undefined, expected: string): boolean {
    if (typeof actual !== "string") return false;
    const bytes = Buffer.from(actual);
    const expectedBytes = Buffer.from(expected);
    return bytes.length === expectedBytes.length && timingSafeEqual(bytes, expectedBytes);
}

function json(response: ServerResponse, status: number, value: unknown): void {
    response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
    response.end(JSON.stringify(value));
}

function readBody(request: IncomingMessage): Promise<unknown> {
    return new Promise((resolve, reject) => {
        let length = 0;
        const chunks: Buffer[] = [];
        request.on("data", (chunk: Buffer) => {
            length += chunk.length;
            if (length > MAX_COMMAND_BYTES) {
                reject(new FactoryError("command_too_large", `Commands may not exceed ${MAX_COMMAND_BYTES} bytes.`, 413));
                return;
            }
            chunks.push(chunk);
        });
        request.on("error", reject);
        request.on("end", () => {
            if (length > MAX_COMMAND_BYTES) return;
            try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
            catch { reject(new FactoryError("invalid_json", "Request body must be valid JSON.")); }
        });
    });
}

export async function startServer(options: { application: Application; port?: number; assets?: string }) {
    const assetDirectory = options.assets || fileURLToPath(new URL("../web/dist/", import.meta.url));
    if (!existsSync(path.join(assetDirectory, "index.html"))) {
        throw new FactoryError("ui_not_built", "Build the optional web UI before starting its server: npm --prefix web run build.", 500);
    }
    const assets = realpathSync(assetDirectory);
    const sessionToken = randomBytes(32).toString("hex");
    const csrf = randomBytes(32).toString("hex");
    let origin = "";
    let cookieName = "";

    async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setHeader("Referrer-Policy", "no-referrer");
        response.setHeader("Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
        if (request.headers.host !== new URL(origin).host ||
            (request.headers.origin !== undefined && request.headers.origin !== origin) ||
            request.headers["sec-fetch-site"] === "cross-site") {
            throw new FactoryError("origin_forbidden", "Only the local Software Factory origin is allowed.", 403);
        }
        const url = new URL(request.url || "/", origin);
        if (url.pathname === "/api/session" && request.method === "GET") {
            response.setHeader("Set-Cookie", `${cookieName}=${sessionToken}; HttpOnly; SameSite=Strict; Path=/`);
            json(response, 200, { csrf, project: options.application.registry.project });
            return;
        }
        if (url.pathname.startsWith("/api/")) {
            const cookie = request.headers.cookie?.split(";").map((value) => value.trim())
                .find((value) => value.startsWith(`${cookieName}=`))?.slice(cookieName.length + 1);
            if (!matches(cookie, sessionToken)) throw new FactoryError("session_required", "Reload the local application to establish a session.", 401);
            if (request.method === "GET" && url.pathname === "/api/features") {
                json(response, 200, { project: options.application.registry.project, features: options.application.registry.list().map((feature) => options.application.lifecycle.store.view(feature.id)) });
            } else if (request.method === "GET" && url.pathname === "/api/doctor") {
                json(response, 200, runtimeReport());
            } else if (request.method === "GET" && url.pathname === "/api/configuration") {
                json(response, 200, { configuration: options.application.lifecycle.store.configuration() });
            } else if (request.method === "GET" && url.pathname.startsWith("/api/gates/")) {
                const featureId = url.pathname.slice("/api/gates/".length);
                options.application.registry.get(featureId);
                json(response, 200, { gates: ["design", "plan", "backlog", "dispatch", "complete"].map((stage) => {
                    try { return { available: true, ...options.application.lifecycle.gate(featureId, stage as "design" | "plan" | "backlog" | "dispatch" | "complete") }; }
                    catch (error) {
                        if (!(error instanceof FactoryError)) throw error;
                        return { available: false, stage, reason: error.message, code: error.code };
                    }
                }) });
            } else if (request.method === "POST" && ["/api/commands", "/api/commands/preview"].includes(url.pathname)) {
                if (request.headers.origin !== origin || !matches(request.headers["x-software-factory-csrf"]?.toString(), csrf)) {
                    throw new FactoryError("confirmation_forbidden", "A same-origin application session and anti-forgery token are required.", 403);
                }
                if (request.headers["content-type"]?.split(";")[0]?.trim().toLowerCase() !== "application/json") {
                    throw new FactoryError("invalid_content_type", "Commands must use application/json.", 415);
                }
                const command = await readBody(request);
                const preview = options.application.prepare(command);
                if (url.pathname === "/api/commands/preview") { json(response, 200, preview); return; }
                if (preview.consentKey && request.headers["x-software-factory-scope"] !== preview.consentKey) {
                    throw new FactoryError("scope_confirmation_required", "Confirm the current exact command preview before submitting it.", 409);
                }
                const actor: Actor = { channel: "web", reference: randomUUID() };
                const consent = preview.consentKey
                    ? { granted: true as const, revisionId: preview.consentKey, actor }
                    : undefined;
                json(response, 200, await options.application.dispatch(command, actor, consent));
            } else {
                throw new FactoryError("route_not_found", "API route or method not found.", 404);
            }
            return;
        }
        if (request.method !== "GET") throw new FactoryError("method_not_allowed", "This route is read-only.", 405);
        let pathname: string;
        try { pathname = decodeURIComponent(url.pathname); }
        catch { throw new FactoryError("invalid_path", "Invalid asset path."); }
        if (pathname.includes("\0")) throw new FactoryError("invalid_path", "Invalid asset path.");
        const candidate = path.resolve(assets, pathname === "/" ? "index.html" : pathname.slice(1));
        if (!candidate.startsWith(assets + path.sep) || !existsSync(candidate) || !statSync(candidate).isFile()) {
            throw new FactoryError("asset_not_found", "Asset not found.", 404);
        }
        const resolved = realpathSync(candidate);
        if (!resolved.startsWith(assets + path.sep)) throw new FactoryError("asset_forbidden", "Asset is outside the UI build.", 403);
        const mime: Record<string, string> = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
        response.writeHead(200, {
            "Content-Type": `${mime[path.extname(resolved)] || "application/octet-stream"}; charset=utf-8`,
            "Cache-Control": "no-store",
        });
        response.end(readFileSync(resolved));
    }

    const server = http.createServer((request, response) => {
        void handle(request, response).catch((error: unknown) => {
            if (error instanceof FactoryError) {
                json(response, error.status, { error: { code: error.code, message: error.message } });
            } else {
                console.error("[Software Factory] Request failed:", error);
                json(response, 500, { error: { code: "internal_error", message: "The operation failed. See the local server error; no success was recorded." } });
            }
        });
    });
    server.requestTimeout = 10_000;
    await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(options.port ?? 0, "127.0.0.1", () => {
            server.removeListener("error", reject);
            resolve();
        });
    });
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("The loopback server did not acquire a port.");
    origin = `http://127.0.0.1:${address.port}`;
    cookieName = `software_factory_${address.port}`;
    return {
        url: origin,
        close: () => new Promise<void>((resolve, reject) => {
            server.close((error) => error ? reject(error) : resolve());
            server.closeAllConnections();
        }),
    };
}
